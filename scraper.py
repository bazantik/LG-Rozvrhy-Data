import json
import asyncio
import os
import sys
from datetime import datetime, timezone
from playwright.async_api import async_playwright

# Načtení URL z tajných proměnných v GitHub Secrets
BASE_URL = os.environ.get("BASE_URL")

if not BASE_URL:
    print("CHYBA: Není nastavena tajná proměnná BASE_URL v GitHub Secrets!")
    sys.exit(1)

# Odstraníme případné lomítko na konci pro správné spojování odkazů
BASE_URL = BASE_URL.rstrip("/")

async def scrape_bakalari():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Nastavíme české prostředí prohlížeče
        context = await browser.new_context(locale="cs-CZ")
        page = await context.new_page()
        
        print("Načítám seznam tříd...")
        await page.goto(f"{BASE_URL}/", timeout=60000, wait_until="domcontentloaded")
        await page.wait_for_selector("select")
        
        classes = await page.eval_on_selector_all(
            "select option",
            "options => options.map(o => ({ id: o.value, name: o.innerText })).filter(o => o.id !== '')"
        )
        print(f"Nalezeno {len(classes)} tříd.")

        teachers_data = {}
        days_off_data = {"Actual": {}, "Next": {}}
        weeks = ["Actual", "Next"]

        for week in weeks:
            print(f"\n--- Stahuji data pro týden: {week} ---")
            for cls in classes:
                print(f"Zpracovávám třídu: {cls['name'].strip()} ({week})")
                
                await asyncio.sleep(1)
                
                class_url = f"{BASE_URL}/{week}/Class/{cls['id']}"
                await page.goto(class_url, timeout=60000, wait_until="domcontentloaded")
                
                # 1. BEZPEČNÉ VYTAŽENÍ SVÁTKŮ A VOLNÝCH DNŮ (v try/except)
                try:
                    day_offs = await page.evaluate('''() => {
                        const results = [];
                        // Najdeme řádky dnů v rozvrhu
                        const dayRows = document.querySelectorAll('.bk-timetable-days-wrapper .bk-timetable-row');
                        
                        dayRows.forEach((row, index) => {
                            // Hledáme buňku volna uvnitř řádku dne
                            const dayOffContent = row.querySelector('.dayoff-content');
                            if (dayOffContent) {
                                const nameEl = dayOffContent.querySelector('.dayoff-name');
                                const name = nameEl ? nameEl.innerText.trim() : "Volno";
                                
                                // Index řádku 0..4 přímo odpovídá Pondělí až Pátek:
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

                # 2. STANDARDNÍ VYTAŽENÍ HODIN VÝUKY
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
                            "class_name": cls['name'].strip(),
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

        # OCHRANA: Uložíme jen tehdy, pokud jsme stáhli data alespoň pro 10 učitelů
        if len(teachers_data) > 10:
            timestamp_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            
            export_data = {
                "last_updated": timestamp_iso,
                "days_off": days_off_data,
                "teachers": teachers_data
            }
            with open("ucitele.json", "w", encoding="utf-8") as f:
                json.dump(export_data, f, ensure_ascii=False, indent=4)
            print("Úspěšně hotovo! Data uložena.")
        else:
            print("CHYBA: Staženo příliš málo dat. JSON nebyl přepsán!")

if __name__ == "__main__":
    asyncio.run(scrape_bakalari())
