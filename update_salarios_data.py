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


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def download_file(url: str, output_path: Path) -> None:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    output_path.write_bytes(response.content)


def extract_effective_date(segment: str) -> date | None:
    matches = list(DATE_PATTERN.finditer(segment))
    if not matches:
        return None

    chosen = matches[-1] if re.search(r"ser[áa]\s+de\s+aplicaci[oó]n", segment, flags=re.IGNORECASE) else matches[0]
    month = MONTH_MAP.get(chosen.group(1).lower())
    if month is None:
        return None
    return date(int(chosen.group(2)), month, 1)


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
        for line in text.splitlines():
            cleaned_line = normalize_text(line)
            if cleaned_line.startswith("0101 ") and "JUEZ DE LA CORTE SUPREMA" in cleaned_line:
                digits = re.sub(r"\D", "", cleaned_line)
                if digits:
                    return float(digits)
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
        if "Ministerio de Defensa" not in text and "Reglamentación del Capítulo IV" not in text:
            continue
        if not BOLETIN_RESOLUTION_PATTERN.search(text):
            continue
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


def extract_defensa_scale_total(pdf_bytes: bytes) -> float:
    pdf_path = BASE_DIR / "_tmp_boletin_defensa.pdf"
    txt_path = BASE_DIR / "_tmp_boletin_defensa.txt"
    pdf_path.write_bytes(pdf_bytes)

    try:
        subprocess.run(["pdftotext", "-layout", str(pdf_path), str(txt_path)], check=True, capture_output=True)
        text = txt_path.read_text(encoding="utf-8", errors="ignore")

        lines = [normalize_text(line) for line in text.splitlines() if normalize_text(line)]
        def first_amount(value: str) -> float | None:
            match = re.search(r"\d[\d\.]*", value)
            if match is None:
                return None
            digits = re.sub(r"\D", "", match.group(0))
            return float(digits) if digits else None

        anchor = "Teniente General, Almirante, Brigadier General"
        anchor_prefix = "Teniente General, Almirante, Brigadier"
        for index, line in enumerate(lines):
            if anchor not in line and not line.startswith(anchor_prefix):
                continue

            candidates = [line]
            candidates.extend(lines[index + 1 : index + 6])
            for candidate in candidates:
                amount = first_amount(candidate)
                if amount is not None:
                    return amount

            window = " ".join(lines[index : min(len(lines), index + 6)])
            amount = first_amount(window)
            if amount is not None:
                return amount

        raise RuntimeError("No se pudo extraer el haber mensual de Defensa del anexo.")
    finally:
        if pdf_path.exists():
            pdf_path.unlink()
        if txt_path.exists():
            txt_path.unlink()


def extract_defensa_events() -> pd.DataFrame:
    session = requests.Session()
    detail_urls: list[str] = []

    for year in [2022, 2023, 2024, 2025, 2026]:
        results = boletin_search("Reglamentación del Capítulo IV - Haberes", year)
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
        effective_match = re.search(r"a partir de\s+([a-záéíóúñ]+)(?:\s+de)?\s+(\d{4})", detail_text, flags=re.IGNORECASE)
        if title_match is None or published_match is None or effective_match is None:
            continue

        effective_month = MONTH_MAP.get(effective_match.group(1).lower())
        if effective_month is None:
            continue

        pdf_bytes = extract_boletin_anexo_pdf(session, full_url, anexo_number="1")
        scale_total = extract_defensa_scale_total(pdf_bytes)

        records.append(
            {
                "detail_url": full_url,
                "published_date": published_match.group(1),
                "effective_year": int(effective_match.group(2)),
                "effective_month": effective_month,
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


def main() -> None:
    download_file(INDEC_URL, INDEC_OUTPUT)

    sinep_events = extract_sinep_events()
    sinep_events.to_csv(SINEP_OUTPUT, index=False)

    csjn_events = extract_csjn_events()
    csjn_events.to_csv(CSJN_OUTPUT, index=False)

    defensa_events = extract_defensa_events()
    defensa_events.to_csv(DEFENSA_OUTPUT, index=False)

    print(f"INDEC actualizado: {INDEC_OUTPUT}")
    print(f"Serie SINEP extraida: {SINEP_OUTPUT}")
    print(f"Eventos SINEP guardados: {len(sinep_events)}")
    print(f"Serie judicial extraida: {CSJN_OUTPUT}")
    print(f"Eventos judiciales guardados: {len(csjn_events)}")
    print(f"Serie Defensa/FFAA extraida: {DEFENSA_OUTPUT}")
    print(f"Eventos Defensa/FFAA guardados: {len(defensa_events)}")


if __name__ == "__main__":
    main()