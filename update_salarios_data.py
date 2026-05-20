from __future__ import annotations

import base64
import json
import re
import subprocess
from datetime import date
from io import StringIO
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning


disable_warnings(InsecureRequestWarning)

BASE_DIR = Path(__file__).resolve().parent
INDEC_URL = "https://www.indec.gob.ar/ftp/cuadros/sociedad/variacion_indice_salarios.csv"
UPCN_URL = "https://upcndigital.org/~legislacion/Indices/Aumentos%20Salariales_Actas%20y%20Decretos.htm"
CSJN_PAGE_URL = "https://www.csjn.gov.ar/transparencia/personal-judicial/escala-salarial"
CSJN_DATA_URL = "https://www.csjn.gov.ar/temasGral/data"
BOLETIN_SEARCH_URL = "https://www.boletinoficial.gob.ar/busquedaAvanzada/realizarBusqueda"
BOLETIN_DOWNLOAD_ANEXO_URL = "https://www.boletinoficial.gob.ar/pdf/download_anexo"

INDEC_OUTPUT = BASE_DIR / "variacion_indice_salarios.csv"
SINEP_OUTPUT = BASE_DIR / "sinep_upcn_2022_2026.csv"
CSJN_OUTPUT = BASE_DIR / "judicial_csjn_2022_2026.csv"
DEFENSA_OUTPUT = BASE_DIR / "defensa_ffaa_2022_2026.csv"
SEGURIDAD_JUSTICIA_OUTPUT = BASE_DIR / "seguridad_justicia_2022_2026.csv"

TARGET_YEARS = {"2022", "2023", "2024", "2025", "2026"}
MONTH_MAP = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

PERCENT_PATTERN = re.compile(r"\(\s*(\d+(?:[.,]\d+)?)\s*%\s*\)")
DATE_PATTERN = re.compile(
    r"(?:ser[áa]\s+de\s+aplicaci[oó]n\s+a\s+partir\s+del|a\s+partir\s+del)\s+1\s*[º°]?\s*de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})",
    flags=re.IGNORECASE,
)
CSJN_DETAIL_PATTERN = re.compile(
    r"vigente\s+a\s+partir\s+de\s+([a-záéíóúñ]+)(?:\s+de)?\s+(\d{4})",
    flags=re.IGNORECASE,
)
BOLETIN_RESOLUTION_PATTERN = re.compile(
    r"Resoluci[oó]n\s+Conjunta\s+(\d+)\/(\d{4})",
    flags=re.IGNORECASE,
)
DEFENSA_MONTH_RANGE_PATTERN = re.compile(
    r"meses?\s+de\s+([a-záéíóúñ]+)(?:\s*,\s*[a-záéíóúñ]+){3,}\s+y\s+[a-záéíóúñ]+\s+de\s+(\d{4})",
    flags=re.IGNORECASE,
)
SPECIAL_DEFENSA_SERIES: dict[str, list[tuple[date, float]]] = {
    "https://www.boletinoficial.gob.ar/detalleAviso/primera/340266/20260401?busqueda=1": [
        (date(2026, 1, 1), 2937768.0),
        (date(2026, 2, 1), 3002399.0),
        (date(2026, 3, 1), 3062447.0),
        (date(2026, 4, 1), 3114509.0),
        (date(2026, 5, 1), 3161227.0),
    ],
}


def canonicalize_boletin_url(url: str) -> str:
    return url.replace("&anexos=1", "")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def download_file(url: str, output_path: Path) -> None:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    output_path.write_bytes(response.content)


def extract_effective_date(segment: str) -> date | None:
    matches = list(DATE_PATTERN.finditer(segment))
    if not matches:
        range_match = DEFENSA_MONTH_RANGE_PATTERN.search(segment)
        if range_match is None:
            return None

        month = MONTH_MAP.get(range_match.group(1).lower())
        if month is None:
            return None
        return date(int(range_match.group(2)), month, 1)

    chosen = matches[-1] if re.search(r"ser[áa]\s+de\s+aplicaci[oó]n", segment, flags=re.IGNORECASE) else matches[0]
    month = MONTH_MAP.get(chosen.group(1).lower())
    if month is None:
        return None
    return date(int(chosen.group(2)), month, 1)


def extract_defensa_effective_date(segment: str) -> date | None:
    article_match = re.search(
        r"ART[ÍI]CULO\s+1°[-.]\s+F[ií]jase\s+el\s+“Haber\s+Mensual”\s+.*?a\s+partir\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})",
        segment,
        flags=re.IGNORECASE,
    )
    if article_match is not None:
        month = MONTH_MAP.get(article_match.group(1).lower())
        if month is not None:
            return date(int(article_match.group(2)), month, 1)

    range_match = re.search(
        r"ART[ÍI]CULO\s+1°[-.]\s+F[ií]jase\s+el\s+“Haber\s+Mensual”\s+.*?para\s+los\s+meses\s+de\s+([a-záéíóúñ]+)(?:\s*,\s*[a-záéíóúñ]+){3}\s+y\s+[a-záéíóúñ]+\s+de\s+(\d{4})",
        segment,
        flags=re.IGNORECASE,
    )
    if range_match is not None:
        month = MONTH_MAP.get(range_match.group(1).lower())
        if month is not None:
            return date(int(range_match.group(2)), month, 1)

    return extract_effective_date(segment)


def extract_sinep_events() -> pd.DataFrame:
    response = requests.get(UPCN_URL, timeout=60, verify=False)
    response.raise_for_status()

    table = pd.read_html(StringIO(response.text))[0]
    records: list[dict[str, object]] = []
    event_order = 0

    for row_index, row in table.iterrows():
        row_text = normalize_text(" ".join(str(value) for value in row.tolist() if pd.notna(value)))
        if "SINEP" not in row_text or not any(year in row_text for year in TARGET_YEARS):
            continue

        matches = list(PERCENT_PATTERN.finditer(row_text))
        for match_index, match in enumerate(matches):
            segment_end = matches[match_index + 1].start() if match_index + 1 < len(matches) else len(row_text)
            segment = row_text[match.end() : segment_end]
            effective_date = extract_effective_date(segment)
            if effective_date is None or effective_date < date(2022, 1, 1):
                continue

            records.append(
                {
                    "event_order": event_order,
                    "source_row": int(row_index),
                    "date": effective_date.isoformat(),
                    "increase_pct": float(match.group(1).replace(",", ".")),
                    "source_url": UPCN_URL,
                }
            )
            event_order += 1

    if not records:
        raise RuntimeError("No se encontraron aumentos SINEP en la página de UPCN.")

    events = pd.DataFrame(records)
    events["date"] = pd.to_datetime(events["date"])
    events = events.sort_values(["date", "event_order"]).drop_duplicates(subset=["date"], keep="last")
    events["date"] = events["date"].dt.strftime("%Y-%m-%d")
    return events.reset_index(drop=True)


def extract_pdf_total(pdf_bytes: bytes) -> float:
    pdf_path = BASE_DIR / "_tmp_csjn_scale.pdf"
    txt_path = BASE_DIR / "_tmp_csjn_scale.txt"
    pdf_path.write_bytes(pdf_bytes)

    try:
        subprocess.run(["pdftotext", "-layout", str(pdf_path), str(txt_path)], check=True, capture_output=True)
        text = txt_path.read_text(encoding="utf-8", errors="ignore")

        def parse_amount_token(token: str) -> float | None:
            cleaned = token.replace(" ", "")
            if not re.search(r"\d", cleaned):
                return None
            if "," in cleaned and "." in cleaned:
                cleaned = cleaned.replace(",", "")
            elif "," in cleaned:
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
            try:
                return float(cleaned)
            except ValueError:
                return None

        def last_meaningful_amount(value: str) -> float | None:
            matches = re.findall(r"\d[\d\.,]*", value)
            for match in reversed(matches):
                amount = parse_amount_token(match)
                if amount is not None:
                    return amount
            return None

        lines = [normalize_text(line) for line in text.splitlines() if normalize_text(line)]
        for index, line in enumerate(lines):
            if "JUEZ DE LA CORTE SUPREMA" not in line:
                continue

            candidates = [line]
            candidates.extend(lines[index + 1 : index + 5])
            for candidate in candidates:
                amount = last_meaningful_amount(candidate)
                if amount is not None:
                    return amount

            window = " ".join(lines[index : min(len(lines), index + 5)])
            amount = last_meaningful_amount(window)
            if amount is not None:
                return amount
        raise RuntimeError("No se pudo extraer el total de la escala judicial.")
    finally:
        if pdf_path.exists():
            pdf_path.unlink()
        if txt_path.exists():
            txt_path.unlink()


def extract_csjn_events() -> pd.DataFrame:
    session = requests.Session()
    page_response = session.get(CSJN_PAGE_URL, timeout=60)
    page_response.raise_for_status()

    token_match = re.search(r"token\s*=\s*[\"']([^\"']+)[\"']", page_response.text)
    header_match = re.search(r"header\s*=\s*[\"']([^\"']+)[\"']", page_response.text)
    if token_match is None or header_match is None:
        raise RuntimeError("No se pudo extraer el token CSRF de la página de la CSJN.")

    payload = {
        "draw": 1,
        "columns": [
            {
                "data": "function",
                "name": "",
                "searchable": True,
                "orderable": False,
                "search": {"value": "", "regex": False, "fixed": []},
            }
        ],
        "order": [],
        "start": 0,
        "length": 1000,
        "search": {"value": "", "regex": False, "fixed": []},
        "formBusqueda": {
            "qa": "",
            "nroDoca": "",
            "anioDoca": "",
            "temaBase": "K14",
            "temaPrincipal_a": "K197",
            "subTema_a": "",
            "fechaDesde_a": "",
            "fechaHasta_a": "",
            "nroDoc": "",
            "anioDoc": "",
            "q": "",
            "fechaDesde": "",
            "fechaHasta": "",
        },
    }

    data_response = session.post(
        CSJN_DATA_URL,
        json=payload,
        headers={header_match.group(1): token_match.group(1), "X-Requested-With": "XMLHttpRequest"},
        timeout=60,
    )
    data_response.raise_for_status()
    response_json = data_response.json()

    records: list[dict[str, object]] = []
    for item in response_json.get("data", []):
        detail = normalize_text(str(item.get("detalle", "")))
        detail_match = CSJN_DETAIL_PATTERN.search(detail)
        if detail_match is None:
            continue

        month = MONTH_MAP.get(detail_match.group(1).lower())
        if month is None:
            continue

        effective_date = date(int(detail_match.group(2)), month, 1)
        if effective_date < date(2021, 12, 1):
            continue

        doc_id = int(item["docId"])
        pdf_response = session.get(f"https://www.csjn.gov.ar/documentos/descargar?ID={doc_id}", timeout=60)
        pdf_response.raise_for_status()
        scale_total = extract_pdf_total(pdf_response.content)

        records.append(
            {
                "doc_id": doc_id,
                "published_date": str(item.get("fecha", "")),
                "date": effective_date.isoformat(),
                "detail": detail,
                "scale_total": scale_total,
                "source_url": CSJN_PAGE_URL,
            }
        )

    if not records:
        raise RuntimeError("No se encontraron escalas judiciales en la página de la CSJN.")

    events = pd.DataFrame(records)
    events["published_date"] = pd.to_datetime(events["published_date"], format="%d/%m/%Y", errors="coerce")
    events["date"] = pd.to_datetime(events["date"])
    events = events.sort_values(["date", "published_date", "doc_id"]).drop_duplicates(subset=["date"], keep="last")
    events = events.loc[events["date"] >= pd.Timestamp("2022-01-01")].copy()
    events["increase_pct"] = events["scale_total"].pct_change() * 100
    events["event_order"] = range(len(events))
    events["published_date"] = events["published_date"].dt.strftime("%Y-%m-%d")
    events["date"] = events["date"].dt.strftime("%Y-%m-%d")
    return events[["event_order", "date", "increase_pct", "scale_total", "doc_id", "published_date", "detail", "source_url"]].reset_index(drop=True)


def boletin_search(query: str, year: int) -> list[dict[str, object]]:
    params = {
        "busquedaRubro": False,
        "hayMasResultadosBusqueda": True,
        "ejecutandoLlamadaAsincronicaBusqueda": False,
        "ultimaSeccion": "",
        "filtroPorRubrosSeccion": False,
        "filtroPorRubroBusqueda": False,
        "filtroPorSeccionBusqueda": False,
        "busquedaOriginal": True,
        "ordenamientoSegunda": False,
        "seccionesOriginales": [1, 2, 3],
        "ultimoItemExterno": None,
        "ultimoItemInterno": None,
        "texto": query,
        "rubros": [],
        "nroNorma": "",
        "anioNorma": "",
        "denominacion": "",
        "tipoContratacion": "",
        "anioContratacion": "",
        "nroContratacion": "",
        "fechaDesde": f"01/01/{year}",
        "fechaHasta": f"31/12/{year}",
        "todasLasPalabras": True,
        "comienzaDenominacion": True,
        "seccion": [1, 2, 3],
        "tipoBusqueda": "Avanzada",
        "numeroPagina": 1,
        "ultimoRubro": "",
    }

    response = requests.post(
        BOLETIN_SEARCH_URL,
        data="params=" + quote(json.dumps(params, ensure_ascii=False)) + "&array_volver=%5B%5D",
        headers={"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    html = payload.get("content", {}).get("html", "")

    results: list[dict[str, object]] = []
    for match in re.finditer(r'<a[^>]+href="(/detalleAviso/[^"]+)"[^>]*>(.*?)</a>', html, flags=re.S):
        href = match.group(1).replace("&amp;", "&")
        text = normalize_text(re.sub(r"<[^>]+>", " ", match.group(2)))
        results.append({"href": href, "text": text})

    return results


def extract_boletin_anexo_pdf(session: requests.Session, detail_url: str, anexo_number: str = "1") -> bytes:
    detail_response = session.get(detail_url, timeout=60)
    detail_response.raise_for_status()
    onclick_matches = re.findall(
        r'descargarPDFAnexo\("([^"]+)","([^"]+)",\s*"([^"]+)",\s*"([^"]+)",\s*"([^"]+)"\)',
        detail_response.text,
    )
    if not onclick_matches:
        raise RuntimeError(f"No se encontraron anexos descargables en {detail_url}")

    chosen: tuple[str, str, str, str] | None = None
    for section, nro_anexo, id_anexo, fecha_publicacion, _endpoint in onclick_matches:
        if nro_anexo == anexo_number:
            chosen = (section, nro_anexo, id_anexo, fecha_publicacion)
            break
    if chosen is None:
        raise RuntimeError(f"No se encontró el anexo {anexo_number} en {detail_url}")

    section, nro_anexo, id_anexo, fecha_publicacion = chosen
    download_response = session.post(
        BOLETIN_DOWNLOAD_ANEXO_URL,
        data={
            "seccion": section,
            "nroAnexo": nro_anexo,
            "idAnexo": id_anexo,
            "fechaPublicacion": fecha_publicacion,
        },
        timeout=60,
    )
    download_response.raise_for_status()
    download_json = download_response.json()
    return base64.b64decode(download_json["pdfBase64"])


def extract_defensa_scale_series(pdf_bytes: bytes) -> list[tuple[date, float]]:
    pdf_path = BASE_DIR / "_tmp_boletin_defensa.pdf"
    txt_path = BASE_DIR / "_tmp_boletin_defensa.txt"
    pdf_path.write_bytes(pdf_bytes)

    try:
        subprocess.run(["pdftotext", "-layout", str(pdf_path), str(txt_path)], check=True, capture_output=True)
        text = txt_path.read_text(encoding="utf-8", errors="ignore")

        lines = [normalize_text(line) for line in text.splitlines() if normalize_text(line)]
        anchor = None
        for index, line in enumerate(lines):
            if "Teniente General, Almirante, Brigadier General" in line or line.startswith("Teniente General"):
                anchor = index
                break

        if anchor is None:
            raise RuntimeError("No se pudo ubicar la fila de referencia de Defensa en el anexo.")

        header_text = " ".join(lines[:anchor])
        month_matches = list(
            re.finditer(
                r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\s+(?:de\s+)?(\d{4})",
                header_text,
                flags=re.IGNORECASE,
            )
        )
        if not month_matches:
            raise RuntimeError("No se pudieron detectar los meses de vigencia en el anexo de Defensa.")

        amount_line = None
        for candidate in [lines[anchor], *lines[anchor + 1 : min(len(lines), anchor + 6)]]:
            if re.search(r"\d", candidate):
                amount_line = candidate
                break

        if amount_line is None:
            raise RuntimeError("No se pudieron detectar los importes del anexo de Defensa.")

        amount_tokens = re.findall(r"\d[\d\.]*", amount_line)
        amounts = [float(re.sub(r"\D", "", token)) for token in amount_tokens if re.sub(r"\D", "", token)]
        if not amounts:
            raise RuntimeError("No se pudieron detectar los importes del anexo de Defensa.")

        count = min(len(month_matches), len(amounts))
        series: list[tuple[date, float]] = []
        for month_match, amount in zip(month_matches[:count], amounts[:count]):
            month = MONTH_MAP.get(month_match.group(1).lower())
            if month is None:
                continue
            effective = date(int(month_match.group(2)), month, 1)
            series.append((effective, amount))

        if not series:
            raise RuntimeError("No se pudo construir la serie mensual de Defensa desde el anexo.")

        return series
    finally:
        if pdf_path.exists():
            pdf_path.unlink()
        if txt_path.exists():
            txt_path.unlink()


def extract_seguridad_justicia_scale_total(pdf_bytes: bytes) -> float:
    pdf_path = BASE_DIR / "_tmp_boletin_seguridad.pdf"
    txt_path = BASE_DIR / "_tmp_boletin_seguridad.txt"
    pdf_path.write_bytes(pdf_bytes)

    try:
        subprocess.run(["pdftotext", "-layout", str(pdf_path), str(txt_path)], check=True, capture_output=True)
        text = txt_path.read_text(encoding="utf-8", errors="ignore")

        lines = [normalize_text(line) for line in text.splitlines() if normalize_text(line)]

        def first_meaningful_amount(value: str) -> float | None:
            matches = re.findall(r"\d[\d\.]*", value)
            for match in matches:
                digits = re.sub(r"\D", "", match)
                if digits and len(digits) > 4:
                    return float(digits)
            return None

        anchor_terms = ["HABER MENSUAL", "INSPECTOR GENERAL", "PREFECTO", "COMISARIO GENERAL", "ALCAIDE GENERAL"]
        for index, line in enumerate(lines):
            if not any(term in line for term in anchor_terms):
                continue

            candidates = [line]
            candidates.extend(lines[index + 1 : index + 6])
            for candidate in candidates:
                amount = first_meaningful_amount(candidate)
                if amount is not None:
                    return amount

            window = " ".join(lines[index : min(len(lines), index + 6)])
            amount = first_meaningful_amount(window)
            if amount is not None:
                return amount

        raise RuntimeError("No se pudo extraer el haber mensual de Seguridad y Justicia del anexo.")
    finally:
        if pdf_path.exists():
            pdf_path.unlink()
        if txt_path.exists():
            txt_path.unlink()


def extract_defensa_events() -> pd.DataFrame:
    session = requests.Session()
    detail_urls: list[str] = []

    for year in [2022, 2023, 2024, 2025, 2026]:
        results = boletin_search("Resolución Conjunta Haber Mensual", year)
        for result in results:
            detail_urls.append(canonicalize_boletin_url(result["href"]))

    detail_urls = sorted(set(detail_urls))
    records: list[dict[str, object]] = []

    for detail_url in detail_urls:
        full_url = f"https://www.boletinoficial.gob.ar{detail_url}"
        detail_response = session.get(full_url, timeout=60)
        detail_response.raise_for_status()
        detail_text = normalize_text(detail_response.text)

        title_match = BOLETIN_RESOLUTION_PATTERN.search(detail_text)
        published_match = re.search(r"Fecha de publicaci[oó]n\s*(\d{2}/\d{2}/\d{4})", detail_text, flags=re.IGNORECASE)
        if title_match is None or published_match is None or "Haber Mensual" not in detail_text:
            continue

        scale_series = SPECIAL_DEFENSA_SERIES.get(full_url)
        if scale_series is None:
            for anexo_number in ["1", "2"]:
                try:
                    pdf_bytes = extract_boletin_anexo_pdf(session, full_url, anexo_number=anexo_number)
                    scale_series = extract_defensa_scale_series(pdf_bytes)
                    break
                except RuntimeError:
                    continue

        if scale_series is None:
            continue

        for effective_date, scale_total in scale_series:
            records.append(
                {
                    "detail_url": full_url,
                    "published_date": published_match.group(1),
                    "effective_year": int(effective_date.year),
                    "effective_month": int(effective_date.month),
                    "scale_total": scale_total,
                    "title": title_match.group(0),
                    "source_url": full_url,
                }
            )

    if not records:
        raise RuntimeError("No se encontraron resoluciones de Defensa en el Boletín Oficial.")

    events = pd.DataFrame(records)
    events["published_date"] = pd.to_datetime(events["published_date"], format="%d/%m/%Y", errors="coerce")
    events = events.sort_values(["published_date", "effective_year", "effective_month", "detail_url"])
    events = events.loc[events["published_date"] >= pd.Timestamp("2022-01-01")].copy()
    events["increase_pct"] = events["scale_total"].pct_change() * 100
    events["event_order"] = range(len(events))
    events["published_date"] = events["published_date"].dt.strftime("%Y-%m-%d")
    return events[["event_order", "published_date", "effective_year", "effective_month", "increase_pct", "scale_total", "detail_url", "title", "source_url"]].reset_index(drop=True)


def extract_seguridad_justicia_events() -> pd.DataFrame:
    session = requests.Session()
    detail_urls: list[str] = []

    for year in [2022, 2023, 2024, 2025, 2026]:
        for query in ["Servicio Penitenciario Federal haber mensual", "Haber Mensual personal seguridad y justicia"]:
            results = boletin_search(query, year)
            for result in results:
                detail_urls.append(result["href"])

    detail_urls = sorted(set(detail_urls))
    records: list[dict[str, object]] = []

    for detail_url in detail_urls:
        full_url = f"https://www.boletinoficial.gob.ar{detail_url}"
        detail_response = session.get(full_url, timeout=60)
        detail_response.raise_for_status()
        detail_text = normalize_text(detail_response.text)

        title_match = BOLETIN_RESOLUTION_PATTERN.search(detail_text)
        published_match = re.search(r"Fecha de publicaci[oó]n\s*(\d{2}/\d{2}/\d{4})", detail_text, flags=re.IGNORECASE)
        effective_date = extract_effective_date(detail_text)
        if title_match is None or published_match is None or effective_date is None or "Haber Mensual" not in detail_text:
            continue

        try:
            pdf_bytes = extract_boletin_anexo_pdf(session, full_url, anexo_number="1")
        except RuntimeError:
            continue
        scale_total = extract_seguridad_justicia_scale_total(pdf_bytes)

        records.append(
            {
                "detail_url": full_url,
                "published_date": published_match.group(1),
                "effective_year": int(effective_date.year),
                "effective_month": int(effective_date.month),
                "scale_total": scale_total,
                "title": title_match.group(0),
                "source_url": full_url,
            }
        )

    if not records:
        raise RuntimeError("No se encontraron resoluciones de Seguridad y Justicia en el Boletín Oficial.")

    events = pd.DataFrame(records)
    events["published_date"] = pd.to_datetime(events["published_date"], format="%d/%m/%Y", errors="coerce")
    events = events.sort_values(["published_date", "effective_year", "effective_month", "detail_url"])
    events = events.loc[events["published_date"] >= pd.Timestamp("2022-01-01")].copy()
    events["increase_pct"] = events["scale_total"].pct_change() * 100
    events["event_order"] = range(len(events))
    events["published_date"] = events["published_date"].dt.strftime("%Y-%m-%d")
    return events[["event_order", "published_date", "effective_year", "effective_month", "increase_pct", "scale_total", "detail_url", "title", "source_url"]].reset_index(drop=True)


def main() -> None:
    download_file(INDEC_URL, INDEC_OUTPUT)

    sinep_events = extract_sinep_events()
    sinep_events.to_csv(SINEP_OUTPUT, index=False)

    seguridad_events = extract_seguridad_justicia_events()
    seguridad_events.to_csv(SEGURIDAD_JUSTICIA_OUTPUT, index=False)

    csjn_events = extract_csjn_events()
    csjn_events.to_csv(CSJN_OUTPUT, index=False)

    defensa_events = extract_defensa_events()
    defensa_events.to_csv(DEFENSA_OUTPUT, index=False)

    print(f"INDEC actualizado: {INDEC_OUTPUT}")
    print(f"Serie SINEP extraida: {SINEP_OUTPUT}")
    print(f"Eventos SINEP guardados: {len(sinep_events)}")
    print(f"Serie Seguridad y Justicia extraida: {SEGURIDAD_JUSTICIA_OUTPUT}")
    print(f"Eventos Seguridad y Justicia guardados: {len(seguridad_events)}")
    print(f"Serie judicial extraida: {CSJN_OUTPUT}")
    print(f"Eventos judiciales guardados: {len(csjn_events)}")
    print(f"Serie Defensa/FFAA extraida: {DEFENSA_OUTPUT}")
    print(f"Eventos Defensa/FFAA guardados: {len(defensa_events)}")


if __name__ == "__main__":
    main()