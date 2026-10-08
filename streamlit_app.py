"""Streamlit interface for searching the EU regulation CN-code database."""

from __future__ import annotations

import json
from html import escape
import re
from io import BytesIO
from pathlib import Path

import pdfplumber
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from cn_regulation_search import search


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "cn_regulation.db"


def deserialize_row(row) -> dict:
    record = dict(row)
    record["is_ex"] = bool(record["is_ex"])
    record["referred_articles"] = json.loads(record["referred_articles"])
    record["related_links"] = json.loads(record["related_links"])
    return record


def render_result(record: dict) -> None:
    code_label = f"ex {record['display_code']}" if record["is_ex"] else record["display_code"]
    with st.expander(f"{code_label}  |  {record['section']}", expanded=True):
        if record["section_title"]:
            st.markdown(f"**{record['section_title']}**")
        if record["referred_articles"]:
            st.caption("Referred provision: " + ", ".join(record["referred_articles"]))

        st.write(record["details"])

        link_columns = st.columns([1, 4])
        link_columns[0].link_button("Open EUR-Lex", record["source_url"], use_container_width=True)
        if record["related_links"]:
            with st.popover(f"Related links ({len(record['related_links'])})"):
                for index, link in enumerate(record["related_links"], start=1):
                    st.markdown(f"[{index}. {link}]({link})")


def create_pdf(
    records: list[dict],
    query: str,
    codes_without_results: list[str] | set[str] | None = None,
) -> bytes:
    font_name = "Helvetica"
    unicode_font_available = False
    font_paths = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for font_path in font_paths:
        if font_path.exists():
            font_name = "CNSearchFont"
            unicode_font_available = True
            if font_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
            break

    output = BytesIO()
    styles = getSampleStyleSheet()

    def safe_text(value: object) -> str:
        text = str(value or "")
        if not unicode_font_available:
            text = text.encode("latin-1", "replace").decode("latin-1")
        return escape(text).replace("\n", "<br/>")

    title_style = ParagraphStyle(
        "SearchTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=18,
        leading=22,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#163A5F"),
        spaceAfter=8,
    )
    subtitle_style = ParagraphStyle(
        "SearchSubtitle",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#5B6770"),
        spaceAfter=14,
    )
    code_style = ParagraphStyle(
        "Code",
        parent=styles["Heading3"],
        fontName=font_name,
        fontSize=11,
        leading=14,
        textColor=colors.white,
        spaceAfter=0,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#163A5F"),
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=9,
        leading=12,
        spaceAfter=6,
    )
    link_style = ParagraphStyle(
        "Link",
        parent=body_style,
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#245A8D"),
        spaceAfter=10,
    )

    document = SimpleDocTemplate(
        output,
        pagesize=letter,
        rightMargin=0.6 * inch,
        leftMargin=0.6 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.6 * inch,
        title=f"CN code search results - {query}",
    )
    story = [
        Paragraph("EU CN Code Search", title_style),
        Paragraph(f"Search values: {safe_text(query)}", subtitle_style),
        Paragraph(f"<b>Total matches:</b> {len(records)}", body_style),
        Spacer(1, 8),
    ]

    for index, record in enumerate(records, start=1):
        code_label = f"ex {record['display_code']}" if record["is_ex"] else record["display_code"]
        header = Table(
            [[Paragraph(safe_text(f"{index}. {code_label}"), code_style)]],
            colWidths=[7.25 * inch],
        )
        header.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1F4E78")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.extend([header, Spacer(1, 4), Paragraph(safe_text(record["section"]), section_style)])
        if record["section_title"]:
            story.append(Paragraph(f"<b>Section:</b> {safe_text(record['section_title'])}", body_style))
        if record["referred_articles"]:
            referred = ", ".join(record["referred_articles"])
            story.append(Paragraph(f"<b>Referred provision:</b> {safe_text(referred)}", body_style))
        story.append(Paragraph(safe_text(record["details"]), body_style))
        story.append(Paragraph(f"<b>EUR-Lex:</b> {safe_text(record['source_url'])}", link_style))
        if record["related_links"]:
            links = "<br/>".join(safe_text(link) for link in record["related_links"])
            story.append(Paragraph(f"<b>Related links:</b><br/>{links}", link_style))
        story.append(Spacer(1, 8))

    not_found_codes = sorted(set(codes_without_results or []))
    if not_found_codes:
        story.append(Spacer(1, 8))
        story.append(Paragraph("CN Codes Not Found in Regulation", section_style))
        story.append(
            Paragraph(
                f"<b>Total codes not found:</b> {len(not_found_codes)}",
                body_style,
            )
        )
        for code in not_found_codes:
            story.append(Paragraph(f"• {safe_text(code)}", body_style))

    def draw_page_footer(pdf: canvas.Canvas, document: SimpleDocTemplate) -> None:
        pdf.saveState()
        pdf.setStrokeColor(colors.HexColor("#D9E2F3"))
        pdf.line(document.leftMargin, 0.42 * inch, letter[0] - document.rightMargin, 0.42 * inch)
        pdf.setFont(font_name, 8)
        pdf.setFillColor(colors.HexColor("#667085"))
        pdf.drawString(document.leftMargin, 0.25 * inch, "EU CN Code Search")
        pdf.drawRightString(letter[0] - document.rightMargin, 0.25 * inch, f"Page {document.page}")
        pdf.restoreState()

    document.build(story, onFirstPage=draw_page_footer, onLaterPages=draw_page_footer)
    return output.getvalue()

def parse_manual_queries(query: str) -> list[str]:
    queries = [value.strip() for value in query.split(",") if value.strip()]
    if len(queries) > 10:
        raise ValueError("Enter no more than 10 CN values, separated by commas.")
    return queries


def search_manual_queries(query_values: list[str]) -> tuple[list[dict], list[str], list[str]]:
    records_by_key: dict[tuple[str, str, str], dict] = {}
    errors: list[str] = []
    codes_without_results: list[str] = []

    for query_value in query_values:
        try:
            rows = search(DATABASE_PATH, query_value, False)
        except ValueError as error:
            errors.append(f"{query_value}: {error}")
            continue

        if not rows:
            codes_without_results.append(query_value)
        else:
            for row in rows:
                record = deserialize_row(row)
                key = (record["cn_code"], record["section_id"], record["details"])
                records_by_key[key] = record

    records = sorted(
        records_by_key.values(),
        key=lambda record: (record["cn_code"], record["section"], record["details"]),
    )
    return records, errors, codes_without_results


st.set_page_config(page_title="EU CN Code Search", page_icon="🔎", layout="wide")

st.title("EU CN Code Search")
st.caption("Council Regulation (EU) No 833/2014, consolidated version of 24 July 2026")

tab1, tab2 = st.tabs(["Manual Search", "PDF Upload"])

# Manual Search Tab
with tab1:
    with st.form("cn_search_form"):
        input_column, button_column = st.columns([6, 1])
        query = input_column.text_input(
            "CN code",
            placeholder="Up to 10 values, for example: 70099100, 69139098",
        )
        input_column.caption("Separate multiple CN values with commas.")
        submitted = button_column.form_submit_button("Search", type="primary", use_container_width=True)

    manual_results = st.session_state.get("manual_results")
    if submitted:
        normalized_query = query.strip()
        if not DATABASE_PATH.exists():
            st.session_state.pop("manual_results", None)
            manual_results = None
            st.error("The search database is missing. Run `python cn_regulation_search.py --build` first.")
        elif not normalized_query:
            st.session_state.pop("manual_results", None)
            manual_results = None
            st.warning("Enter at least one CN code or prefix.")
        else:
            try:
                query_values = parse_manual_queries(normalized_query)
            except ValueError as error:
                st.session_state.pop("manual_results", None)
                manual_results = None
                st.warning(str(error))
            else:
                if not query_values:
                    st.session_state.pop("manual_results", None)
                    manual_results = None
                    st.warning("Enter at least one CN code or prefix.")
                else:
                    manual_results = {
                        "query_values": query_values,
                        "records": [],
                        "search_errors": [],
                        "codes_without_results": [],
                    }
                    records, search_errors, codes_without_results = search_manual_queries(query_values)
                    manual_results.update(
                        records=records,
                        search_errors=search_errors,
                        codes_without_results=codes_without_results,
                    )
                    st.session_state["manual_results"] = manual_results

    if manual_results is not None:
        records = manual_results["records"]
        query_values = manual_results["query_values"]
        search_errors = manual_results["search_errors"]
        codes_without_results = manual_results["codes_without_results"]
        if search_errors:
            st.warning("Some values were skipped:\n" + "\n".join(search_errors))
        if not records:
            st.info("No matching CN-code references were found in this regulation.")
        else:
            article_count = sum(record["record_type"] == "article" for record in records)
            annex_count = len(records) - article_count
            metric_columns = st.columns(3)
            metric_columns[0].metric("Matches", len(records))
            metric_columns[1].metric("Article provisions", article_count)
            metric_columns[2].metric("Annex entries", annex_count)
            query_filename = "_".join(re.sub(r"\D", "", value) for value in query_values)

            st.download_button(
                "Download results as PDF",
                create_pdf(records, ", ".join(query_values), codes_without_results),
                file_name=f"cn_{query_filename}_results.pdf",
                mime="application/pdf",
                key="manual_pdf_download",
            )

            for record in records:
                render_result(record)

        if codes_without_results:
            st.divider()
            st.subheader("⚠️ CN Codes Not Found in Regulation")
            st.caption(f"{len(codes_without_results)} code(s) do not have matching entries in the EU regulation database.")

            codes_list = sorted(codes_without_results)
            col1, col2 = st.columns([2, 3])
            with col1:
                st.write("**Codes not found:**")
                for code in codes_list:
                    st.write(f"• {code}")
            with col2:
                st.write("**Summary:**")
                st.metric("Total codes not found", len(codes_list))

# PDF Upload Tab
with tab2:
    uploaded_file = st.file_uploader("Upload PDF to extract CN codes", type="pdf")

    if uploaded_file:
        try:
            # Extract text from PDF
            pdf_text = ""
            with pdfplumber.open(uploaded_file) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        pdf_text += page_text + "\n"

            st.success(f"✓ PDF loaded: {uploaded_file.name}")

            # Extract CN codes from the text using regex
            cn_pattern = r"(?:ex\s+)?\d{4}(?:\s+\d{2}){0,2}"
            codes_found = re.findall(cn_pattern, pdf_text)

            if not codes_found:
                st.warning("No CN codes found in the PDF.")
            else:
                st.info(f"Found {len(codes_found)} CN code references in the PDF")

                # Search each unique code and collect results
                all_records = []
                unique_codes = set()
                codes_with_results = set()
                codes_without_results = set()

                for code in codes_found:
                    code_stripped = code.strip()
                    if code_stripped not in unique_codes:
                        unique_codes.add(code_stripped)
                        try:
                            records = [deserialize_row(row) for row in search(DATABASE_PATH, code_stripped, False)]
                            if records:
                                codes_with_results.add(code_stripped)
                                all_records.extend(records)
                            else:
                                codes_without_results.add(code_stripped)
                        except ValueError:
                            codes_without_results.add(code_stripped)

                # Remove duplicates based on cn_code and section_id
                unique_records = list({(r['cn_code'], r['section_id']): r for r in all_records}.values())

                if not unique_records and not codes_without_results:
                    st.info("No matching CN-code references found in the regulation for the codes in the PDF.")
                elif unique_records:
                    article_count = sum(r["record_type"] == "article" for r in unique_records)
                    annex_count = len(unique_records) - article_count

                    metric_columns = st.columns(3)
                    metric_columns[0].metric("Unique CN Codes Found", len(unique_codes))
                    metric_columns[1].metric("Article provisions", article_count)
                    metric_columns[2].metric("Annex entries", annex_count)

                    st.divider()

                    st.download_button(
                        "Download results as PDF",
                        create_pdf(
                            unique_records,
                            ", ".join(sorted(unique_codes)),
                            codes_without_results,
                        ),
                        file_name=f"cn_pdf_{uploaded_file.name.rsplit('.', 1)[0]}_results.pdf",
                        mime="application/pdf",
                    )

                    st.subheader("Search Results")
                    for record in unique_records:
                        render_result(record)

                # Display codes not found in the regulation
                if codes_without_results:
                    st.divider()
                    st.subheader("⚠️ CN Codes Not Found in Regulation")
                    st.caption(f"{len(codes_without_results)} code(s) from the PDF do not have matching entries in the EU regulation database.")

                    # Display codes in a formatted way
                    codes_list = sorted(codes_without_results)
                    col1, col2 = st.columns([2, 3])
                    with col1:
                        st.write("**Codes not found:**")
                        for code in codes_list:
                            st.write(f"• {code}")
                    with col2:
                        st.write("**Summary:**")
                        st.metric("Total codes not found", len(codes_list))

        except Exception as e:
            st.error(f"Error processing PDF: {str(e)}")

