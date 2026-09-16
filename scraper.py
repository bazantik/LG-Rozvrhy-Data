import json
import asyncio
from datetime import datetime, timezone
from playwright.async_api import async_playwright

async def scrape_bakalari():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        print("Načítám seznam tříd...")
        await page.goto("https://is.gymjc.cz/bakaweb/Timetable/Public/", timeout=60000, wait_until="domcontentloaded")
        await page.wait_for_selector("select")
        
        classes = await page.eval_on_selector_all(
            "select option",
            "options => options.map(o => ({ id: o.value, name: o.innerText })).filter(o => o.id !== '')"
        )
        print(f"Nalezeno {len(classes)} tříd.")

        teachers_data = {}
        weeks = ["Actual", "Next"]

        for week in weeks:
            print(f"\n--- Stahuji data pro týden: {week} ---")
            for cls in classes:
                print(f"Zpracovávám třídu: {cls['name'].strip()} ({week})")
                
                await asyncio.sleep(1)
                
                class_url = f"https://is.gymjc.cz/bakaweb/Timetable/Public/{week}/Class/{cls['id']}"
                await page.goto(class_url, timeout=60000, wait_until="domcontentloaded")
                
                try:
                    await page.wait_for_selector('.day-item-hover', timeout=3000)
                except:
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
            
            # NOVÉ: Uložíme čistý UTC čas v ISO formátu
            timestamp_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            
            export_data = {
                "last_updated": timestamp_iso,
                "teachers": teachers_data
            }
            with open("ucitele.json", "w", encoding="utf-8") as f:
                json.dump(export_data, f, ensure_ascii=False, indent=4)
            print("Úspěšně hotovo! Data uložena.")
        else:
            print("CHYBA: Staženo příliš málo dat. JSON nebyl přepsán!")

if __name__ == "__main__":
    asyncio.run(scrape_bakalari())
