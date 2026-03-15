#!/usr/bin/env python3
"""
agenda_to_ics.py – extrait les cours de la vue print.php d’un agenda INSA et les convertit en iCal.

Usage :
    python agenda_to_ics.py --cal 2025-GM4 --date 20260202 --output gm4.ics

- --cal : identifiant du calendrier (ex. « 2025-GM4 »).
- --date : une date de la semaine au format AAAAMMJJ (la page print couvre toute la semaine correspondante).
- --output : chemin du fichier .ics à créer (ou rien pour envoyer sur la sortie standard).
"""

import argparse, datetime as _dt, html, re, sys
from typing import Iterable, List, Optional
import requests
from bs4 import BeautifulSoup
import hashlib

MONTHS = {
    "Janvier": 1, "Février": 2, "Fevrier": 2, "Mars": 3, "Avril": 4, "Mai": 5,
    "Juin": 6, "Juillet": 7, "Août": 8, "Aout": 8, "Septembre": 9,
    "Octobre": 10, "Novembre": 11, "Décembre": 12, "Decembre": 12,
}

RE_DAY_HEADER = re.compile(r"^(Lundi|Mardi|Mercredi|Jeudi|Vendredi|Samedi|Dimanche)\s+(\d+)\s+([A-Za-zéûêîôèàçÉûÊÔÎ]+)")
RE_TIME = re.compile(r"^Heure:\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
RE_TIME_VALUE = re.compile(r"^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$")
RE_LOCATION = re.compile(r"^Lieu:\s*(.*)")
RE_SUMMARY = re.compile(r"^Résumé:\s*(.*)")

def fetch_print_view(cal_id: str, date: str) -> str:
    url = f"https://agendas.insa-rouen.fr/print.php?cpath=&getdate={date}&cal[]={cal_id}"
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/118.0.0.0 Safari/537.36"),
        "Referer": "https://agendas.insa-rouen.fr/",
    }
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    # The agenda endpoint now serves UTF-8; keep requests auto-detection.
    if resp.encoding is None:
        resp.encoding = resp.apparent_encoding
    return resp.text

def normalize(text: str) -> str:
    return " ".join(html.unescape(text).split())

def parse_events(html_content: str, week_start: _dt.date) -> List[dict]:
    soup = BeautifulSoup(html_content, "html.parser")
    lines = soup.get_text("\n").split("\n")
    events, current_date = [], None
    i = 0

    def next_non_empty(idx: int):
        while idx < len(lines):
            value = normalize(lines[idx])
            if value:
                return value, idx + 1
            idx += 1
        return None, idx

    while i < len(lines):
        line = normalize(lines[i])
        if not line:
            i += 1; continue
        m_day = RE_DAY_HEADER.match(line)
        if m_day:
            _, day_str, month_name = m_day.groups()
            day = int(day_str)
            month = MONTHS.get(month_name, week_start.month)
            current_date = _dt.date(week_start.year, month, day)
            i += 1; continue

        # Support both formats:
        # - "Heure: 08:30 - 09:30"
        # - "Heure:" then next line "08:30 - 09:30"
        time_match = RE_TIME.match(line)
        if not time_match and line == "Heure:" and current_date:
            time_value, next_i = next_non_empty(i + 1)
            if time_value:
                time_match = RE_TIME_VALUE.match(time_value)
                if time_match:
                    i = next_i - 1

        if time_match and current_date:
            start_str, end_str = time_match.groups()
            sh, sm = map(int, start_str.split(":"))
            eh, em = map(int, end_str.split(":"))
            start = _dt.datetime.combine(current_date, _dt.time(sh, sm))
            end = _dt.datetime.combine(current_date, _dt.time(eh, em))
            if end < start:
                end += _dt.timedelta(days=1)
            event = {"start": start, "end": end, "summary": None, "location": None}
            j = i + 1
            while j < len(lines):
                nxt = normalize(lines[j])
                if not nxt:
                    j += 1; continue
                if RE_TIME.match(nxt) or nxt == "Heure:" or RE_DAY_HEADER.match(nxt):
                    break
                loc_m = RE_LOCATION.match(nxt)
                sum_m = RE_SUMMARY.match(nxt)
                if loc_m:
                    loc_inline = loc_m.group(1).strip()
                    if loc_inline:
                        event["location"] = loc_inline
                    else:
                        loc_value, next_j = next_non_empty(j + 1)
                        if loc_value and loc_value not in {"Heure:", "Lieu:", "Résumé:"}:
                            event["location"] = loc_value
                        j = next_j - 1
                elif sum_m:
                    sum_inline = sum_m.group(1).strip()
                    if sum_inline:
                        event["summary"] = sum_inline
                    else:
                        sum_value, next_j = next_non_empty(j + 1)
                        if sum_value and sum_value not in {"Heure:", "Lieu:", "Résumé:"}:
                            event["summary"] = sum_value
                        j = next_j - 1
                j += 1
            events.append(event)
            i = j; continue
        i += 1
    return events

def to_ical(events: Iterable[dict], cal_name: str, tzid="Europe/Paris") -> str:
    parts = ["BEGIN:VCALENDAR", "VERSION:2.0", "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{cal_name}"]
    for ev in events:
        start = ev["start"].strftime("%Y%m%dT%H%M%S")
        end = ev["end"].strftime("%Y%m%dT%H%M%S")
        summary = ev.get("summary") or "Cours"
        loc = ev.get("location") or ""
        dtstamp = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        uid_source = f"{cal_name}|{start}|{end}|{summary}|{loc}".encode("utf-8")
        uid = f"{hashlib.sha256(uid_source).hexdigest()[:32]}@insa"
        parts += [
            "BEGIN:VEVENT",
            f"DTSTART;TZID={tzid}:{start}",
            f"DTEND;TZID={tzid}:{end}",
            f"DTSTAMP:{dtstamp}",
            f"UID:{uid}",
            f"SUMMARY:{summary}",
        ]
        if loc:
            parts.append(f"LOCATION:{loc}")
        parts.append("END:VEVENT")
    parts.append("END:VCALENDAR")
    return "\r\n".join(parts) + "\r\n"

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cal", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--output")
    args = p.parse_args()
    week_start = _dt.datetime.strptime(args.date, "%Y%m%d").date()
    html = fetch_print_view(args.cal, args.date)
    events = parse_events(html, week_start)
    ical = to_ical(events, args.cal)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(ical)
        print(f"Wrote {len(events)} events to {args.output}")
    else:
        print(ical)

if __name__ == "__main__":
    main()
