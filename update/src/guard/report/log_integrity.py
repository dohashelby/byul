"""Pure presentation of reference collector events, separate from RPT.

This artifact does not add observations to the stored Report object. Messages
are JSON-quoted inside escaped cells so CR/LF and HTML-looking values remain
visible data, without manufacturing rows, attributes or executable markup.
"""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser
import json


def _cells(event):
    return [str(event["event_index"]), event["process"], event["level"],
            f"{event['source']}:{event['source_line']}",
            json.dumps(event["message"], ensure_ascii=False), event["raw_decoded_sha256"]]


def render_log_integrity(collection: dict) -> str:
    out = ["<!doctype html><html lang='ko'><head><meta charset='utf-8'>",
           "<title>OBS-09 reference collector 증거</title></head><body>",
           "<h1>OBS-09 제한된 reference collector 표시</h1>",
           "<p>기업 collector·인증 주체·RPT 상세 표시를 검증한 결과가 아닙니다. 메시지는 비신뢰 데이터입니다.</p>",
           "<table><thead><tr><th>사건</th><th>프로세스</th><th>수준</th><th>소스</th><th>비신뢰 메시지</th><th>원본 참조</th></tr></thead><tbody>"]
    for event in collection["events"]:
        out.append(f'<tr data-event-index="{event["event_index"]}">')
        out.extend("<td><code>" + escape(cell) + "</code></td>" for cell in _cells(event))
        out.append("</tr>")
    out.append("</tbody></table><h2>수집 한계</h2><ul>")
    out.extend("<li>" + escape(gap) + "</li>" for gap in collection["gaps"])
    out.append("</ul></body></html>")
    return "\n".join(out)


class _Display(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell = [], None, None
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "iframe", "object", "embed"}:
            self.invalid = True
        if tag == "tr" and dict(attrs).get("data-event-index") is not None:
            if self.row is not None:
                self.invalid = True
            self.row = [dict(attrs)["data-event-index"], []]
        elif tag == "td" and self.row is not None:
            self.cell = ""

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        if tag == "td" and self.row is not None and self.cell is not None:
            self.row[1].append(self.cell)
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def verify_log_display(document: str, collection: dict) -> bool:
    parser = _Display()
    try:
        parser.feed(document)
        parser.close()
        expected = [[str(event["event_index"]), _cells(event)] for event in collection["events"]]
        return bool(not parser.invalid and parser.rows == expected and parser.row is None)
    except (KeyError, TypeError, ValueError):
        return False
