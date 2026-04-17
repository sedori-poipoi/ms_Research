# Manual Checks

Networked and browser-based probes live here so `python3 -m unittest discover` stays stable.

Kept probes:

- `amazon`: Amazon keyword search sanity check for live SP-API responses
- `jan`: Yodobashi JAN extraction check from a real product page

Quick commands:

```bash
python3 -m manual_checks amazon
python3 -m manual_checks jan
python3 -m manual_checks jan https://www.yodobashi.com/product/100000001009510449/
python3 -m manual_checks jan https://www.yodobashi.com/product/200000000000000001/ --headless --wait 3
```

These commands should be run from the repository root:

```bash
cd /Users/nagi_mi/ラフな会話フォルダ/ms_research_dev
```

Use these probes when you want to inspect live API responses or real page HTML.

`jan` options:

- Pass a product URL to inspect any Yodobashi product page.
- Add `--headless` when you do not need a visible browser window.
- Add `--wait 3` style options when the page needs more or less time before HTML inspection.
