import re
import logging

logger = logging.getLogger(__name__)

class ProductMatcher:
    @staticmethod
    def extract_units(text):
        """
        Extract numerical values and their units from text.
        Returns a set of tuples like (value, unit).
        Example: "1620g" -> (1620.0, "g")
        """
        # Patterns for common units in Japanese e-commerce
        # g, kg, ml, l, 個, 枚, 袋, 本, セット
        patterns = [
            r'(\d+(?:\.\d+)?)\s*(g|kg|ml|l|リットル|キログラム|グラム|個|枚|袋|本|セット|パック|box|箱|束|切|ui)',
        ]
        
        # Convert to lowercase for matching
        text_lower = text.lower()
        found = []
        
        for pattern in patterns:
            matches = re.findall(pattern, text_lower)
            for val, unit in matches:
                val_float = float(val)
                # Normalize units
                if unit in ['kg', 'キログラム']:
                    val_float *= 1000
                    unit = 'g'
                if unit in ['l', 'リットル']:
                    val_float *= 1000
                    unit = 'ml'
                if unit in ['グラム']: unit = 'g'
                
                found.append((val_float, unit))
        
        return found

    @staticmethod
    def get_match_score(source_title, target_title, source_brand="不明"):
        """
        Calculates a compatibility score (0-100).
        Strictly penalizes unit mismatches.
        """
        score = 100
        
        source_units = ProductMatcher.extract_units(source_title)
        target_units = ProductMatcher.extract_units(target_title)
        
        # 1. Strict Unit/Volume Check
        if source_units:
            # If source has units, target MUST have at least one matching unit
            match_found = False
            for s_val, s_unit in source_units:
                # Check if this specific volume exists in target
                for t_val, t_unit in target_units:
                    if s_unit == t_unit:
                        if abs(s_val - t_val) < 0.01: # Float epsilon
                            match_found = True
                            break
                if match_found: break
            
            if not match_found and target_units:
                # If target has DIFFERENT units, it's almost certainly a mismatch
                logger.info(f"Unit mismatch: Source {source_units} vs Target {target_units}")
                return 0
            elif not match_found:
                # Target has no units mentioned, lower the confidence
                score -= 40
        
        # 2. Key Keyword Check
        # Important words that change the meaning of the product
        key_keywords = [
            "キューブ", "粉末", "液体", "詰替", "本体", "セット", "ケース", "訳あり",
            "トリートメント", "cc", "ジェル", "リキッド", "パウダー", "クリーム", "バーム", 
            "ペンシル", "uv", "シルク", "ウォータープルーフ", "マット", "パール"
        ]
        s_title_lower = source_title.lower()
        t_title_lower = target_title.lower()
        
        for kw in key_keywords:
            if kw in s_title_lower and kw not in t_title_lower:
                score -= 40  # Heavy penalty for missing key variation
            if kw not in s_title_lower and kw in t_title_lower:
                score -= 40
                
        # 3. Numeric/Color Code check (like 01, 02)
        # Find 2-digit numbers usually representing color codes in cosmetics
        import re
        s_codes = set(re.findall(r'\b\d{2,3}\b', source_title))
        t_codes = set(re.findall(r'\b\d{2,3}\b', target_title))
        if s_codes and t_codes:
            # If both have codes, and they don't intersect, it's likely a different color
            if not s_codes.intersection(t_codes):
                score -= 60
                logger.info(f"Color/Variant code mismatch: {s_codes} vs {t_codes}")

        # 3. Simple fuzzy title check (remaining parts)
        # (This is a placeholder for more complex Jaccard or Levenshtein if needed)
        # Just check if brand exists if provided
        if source_brand != "不明" and source_brand.lower() not in target_title.lower():
            score -= 20

        return max(0, score)

    @classmethod
    def find_best_match(cls, source_item, amazon_candidates):
        """
        Evaluates candidates and returns the best one if it meets the threshold.
        source_item: {'title': '...', 'brand': '...'}
        amazon_candidates: list of {'asin': '...', 'title': '...', 'brand': '...'}
        """
        if not amazon_candidates:
            return None
        
        best_candidate = None
        max_score = 0
        threshold = 70 # Minimum score to accept a match
        
        for cand in amazon_candidates:
            score = cls.get_match_score(
                source_item['title'], 
                cand['title'], 
                source_brand=source_item.get('brand', '不明')
            )
            
            logger.info(f"Matching candidate: {cand['asin']} | Score: {score} | Title: {cand['title'][:40]}...")
            
            if score > max_score:
                max_score = score
                best_candidate = cand
        
        if max_score >= threshold:
            best_candidate['match_score'] = max_score
            return best_candidate
        
        return None
