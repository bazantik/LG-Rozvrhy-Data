import json
import asyncio
import os
import sys
from datetime import datetime, timezone
from playwright.async_api import async_playwright

BASE_URL = os.environ.get("BASE_URL")

if not BASE_URL:
    print("CHYBA: Není nastavena tajná proměnná BASE_URL v GitHub Secrets!")
    sys.exit(1)

BASE_URL = BASE_URL.rstrip("/")

# Pomocná funkce pro načtení stránky s rychlým opakováním při chybě
async def safe_goto(page, url, retries=2, timeout=30000):
    for attempt in range(1, retries + 1):
        try:
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            return True
        except Exception as e:
            print(f"⚠️ Pokus {attempt}/{retries} pro {url} selhal: {e}")
            if attempt < retries:
                await asyncio.sleep(4)
            else:
                return False

async def scrape_bakalari():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        context = await browser.new_context(
            locale="cs-CZ",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            ignore_https_errors=True
        )
        page = await context.new_page()
        
        print("Načítám seznam tříd...")
        initial_ok = await safe_goto(page, f"{BASE_URL}/", retries=3, timeout=40000)
        if not initial_ok:
            print("❌ Server školy neodpovídá na úvodní stránce. Ukončuji běh – stávající data zůstanou beze změny.")
            await browser.close()
            sys.exit(0)
        
        try:
            await page.wait_for_selector("select", timeout=15000)
        except Exception:
            print("❌ Nepodařilo se nalézt výběr tříd na úvodní stránce.")
            await browser.close()
            sys.exit(0)
        
        classes = await page.eval_on_selector_all(
            "select option",
            "options => options.map(o => ({ id: o.value, name: o.innerText })).filter(o => o.id !== '')"
        )
        print(f"Nalezeno {len(classes)} tříd.")

        teachers_data = {}
        days_off_data = {"Actual": {}, "Next": {}}
        events_data = {"Actual": {}, "Next": {}}
        weeks = ["Actual", "Next"]

        for week in weeks:
            print(f"\n--- Stahuji data pro týden: {week} ---")
            for cls in classes:
                class_name = cls['name'].strip()
                print(f"Zpracovávám třídu: {class_name} ({week})")
                
                await asyncio.sleep(0.5)
                
                class_url = f"{BASE_URL}/{week}/Class/{cls['id']}"
                class_loaded = await safe_goto(page, class_url, retries=2, timeout=25000)
                
                # PRINCIP VŠECHNO NEBO NIC: Pokud třídu nelze načíst, okamžitě zastavíme celý skript
                if not class_loaded:
                    print(f"❌ Kritická chyba: Třídu {class_name} se nepodařilo načíst ani po opakovaném pokusu.")
                    print("Zastavuji celý skript, aby nedošlo k uložení neúplného rozvrhu. Stávající JSON na serveru zůstane zachován.")
                    await browser.close()
                    sys.exit(0)
                
                # 1. Celodenní dny volna / státní svátky
                try:
                    day_offs = await page.evaluate('''() => {
                        const results = [];
                        const dayRows = document.querySelectorAll('.bk-timetable-days-wrapper .bk-timetable-row');
                        
                        dayRows.forEach((row, index) => {
                            const dayOffContent = row.querySelector('.dayoff-content');
                            if (dayOffContent) {
                                const nameEl = dayOffContent.querySelector('.dayoff-name');
                                const name = nameEl ? nameEl.innerText.trim() : "Volno";
                                
                                if (index >= 0 && index < 5) {
                                    results.push({ dayIndex: index, name: name });
                                }
                            }
                        });
                        return results;
                    }''')

                    for item in day_offs:
                        d_idx = str(item["dayIndex"])
                        if d_idx not in days_off_data[week]:
                            days_off_data[week][d_idx] = item["name"]
                            print(f"🎉 Nalezen den volna ({week}, den {d_idx}): {item['name']}")
                except Exception:
                    pass

                # 2. Třídní a školní akce (absence třídy – exkurze, kurzy atd.)
                try:
                    class_events = await page.evaluate('''() => {
                        const results = [];
                        const rows = document.querySelectorAll('.bk-timetable-days-wrapper .bk-timetable-row');
                        
                        rows.forEach((row, index) => {
                            if (index < 0 || index >= 5) return;

                            const dayAbbrev = row.querySelector('.bk-day-day')?.innerText.trim() || "";
                            const date = row.querySelector('.bk-day-date')?.innerText.trim() || "";
                            
                            const absences = row.querySelectorAll('.bk-timetable-absence');
                            absences.forEach(abs => {
                                const detailStr = abs.getAttribute('data-detail');
                                let type = "";
                                let description = "";
                                let time = "";
                                let day = "";

                                if (detailStr) {
                                    try {
                                        const detail = JSON.parse(detailStr);
                                        type = detail.absentinfo || detail.type || "";
                                        description = detail.removedinfo || detail.Name || detail.description || "";
                                        time = detail.time || "";
                                        day = detail.day || "";
                                    } catch (e) {}
                                }

                                if (!description) {
                                    const textNode = abs.querySelector('.absence-info') || abs.querySelector('.middle') || abs;
                                    description = textNode ? textNode.innerText.replace(/\\s+/g, ' ').trim() : "Mimo školu";
                                }

                                if (!type) {
                                    type = "Akce";
                                }

                                results.push({
                                    day_index: index,
                                    day_abbrev: dayAbbrev,
                                    date: date,
                                    type: type,
                                    description: description,
                                    day: day,
                                    time: time
                                });
                            });
                        });
                        return results;
                    }''')

                    for ev in class_events:
                        if class_name not in events_data[week]:
                            events_data[week][class_name] = []
                        
                        if ev not in events_data[week][class_name]:
                            events_data[week][class_name].append(ev)
                            print(f"📌 Nalezena akce ({week}, {class_name}): {ev['description']} ({ev['date']} {ev['time']})")
                except Exception:
                    pass

                # 3. Standardní vyučovací hodiny
                try:
                    await page.wait_for_selector('.day-item-hover', timeout=3000)
                except Exception:
                    continue
                    
                cells_data = await page.evaluate('''() => {
                    const results = [];
                    const cells = document.querySelectorAll('.day-item-hover');
                    
                    cells.forEach(cell => {
                        const detailStr = cell.getAttribute('data-detail');
                        if (detailStr) {
                            try {
                                const detail = JSON.parse(detailStr);
                                
                                const subjAbbrevNode = cell.querySelector('.middle div');
                                detail.subject_abbrev = subjAbbrevNode ? subjAbbrevNode.innerText.trim() : "";
                                
                                const teacherAbbrevNode = cell.querySelector('.teacher-name');
                                detail.teacher_abbrev = teacherAbbrevNode ? teacherAbbrevNode.innerText.trim() : "";
                                
                                results.push(detail);
                            } catch (e) {}
                        }
                    });
                    return results;
                }''')

                for detail in cells_data:
                    teacher = detail.get("teacher")
                    if teacher:
                        if teacher not in teachers_data:
                            teachers_data[teacher] = []
                        
                        room_full = detail.get("room", "")
                        room_abbrev = room_full.split(" - ")[0] if room_full else ""
                        
                        teachers_data[teacher].append({
                            "week": week,
                            "class_name": class_name,
                            "subject": detail.get("subjecttext", ""),
                            "subject_abbrev": detail.get("subject_abbrev", ""),
                            "teacher_abbrev": detail.get("teacher_abbrev", ""),
                            "room": room_full,
                            "room_abbrev": room_abbrev,
                            "day": detail.get("day", ""),
                            "time": detail.get("time", ""),
                            "group": detail.get("group", "")
                        })

        await browser.close()

        # Uložíme data POUZE tehdy, pokud proběhlo kompletní stažení
        if len(teachers_data) > 10:
            timestamp_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            export_data = {
                "last_updated": timestamp_iso,
                "days_off": days_off_data,
                "events": events_data,
                "teachers": teachers_data
            }
            with open("ucitele.json", "w", encoding="utf-8") as f:
                json.dump(export_data, f, ensure_ascii=False, indent=4)
            print(f"\nÚspěšně hotovo! Data uložena k času: {timestamp_iso}")
        else:
            print("CHYBA: Staženo příliš málo dat. JSON nebyl přepsán!")

if __name__ == "__main__":
    asyncio.run(scrape_bakalari())
