# EU regulation CN-code search

The extractor reads the saved EUR-Lex HTML and creates:

- `cn_regulation.json` for use by applications and APIs.
- `cn_regulation.db` for indexed SQLite searches.

Each record contains the normalized CN code, whether it is explicitly marked `ex`, the article or annex, the provision text, and a direct EUR-Lex link. Annex records also include `section_title` and `referred_articles`, for example `List of goods and technology referred to in Article 3i` and `["Article 3i"]` for Annex XXI.

## Build the data

```bash
python cn_regulation_search.py --build
```

## Search

Search a code or prefix. Spaces are optional. Searches use the first four
digits, and for inputs with at least six digits also include the first six
digits. For example, `39231090` returns entries under `3923` as well as
entries matching `392310`:

```bash
python cn_regulation_search.py "2901 10 00"
python cn_regulation_search.py 2901
python cn_regulation_search.py 39231090
```

Search only entries explicitly marked `ex`:

```bash
python cn_regulation_search.py "ex 2710 19 83"
python cn_regulation_search.py 2710 --ex-only
```

Use `--html`, `--db`, or `--json` to override the default file paths.

## Streamlit interface

Install dependencies and start the web interface:

```bash
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

Enter a full CN code or its first four or more digits. The manual search also accepts up to 10 comma-separated values, for example `70099100, 69139098`; matching results are combined and duplicate entries are shown only once. The interface returns every matching article provision and annex entry, including `ex` status, annex titles, referred articles, and EUR-Lex links.
