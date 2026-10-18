# Nibe Price Optimizer

*[English](README.md)*

En Home Assistant-integration som tar reda på – **med din egen data** – hur en Nibe-värmepump
bör styras efter Nord Pools spotpris, innan den får röra pumpen.

1. **Provperiod (standard 14 dagar, ingenting skrivs till pumpen).** Integrationen lär sig hur
   *din* pump och ditt hus beter sig och kör flera varmvatten- och värmestrategier parallellt
   som torrkörningar ("skuggläge").
2. **Rapport.** Varje strategi spelas upp genom er verkliga varmvattenförbrukning och de
   verkliga priserna, inklusive nätkostnaden du anger. Du får kWh, kostnad, antal laddningar,
   COP per temperaturband och en rekommendation.
3. **Skarp styrning (valfritt).** Välj strategi och slå på en switch. Stänger du av återställs
   pumpen till ett neutralt läge.

> Status: **0.1 – beta.** Utvecklad och verifierad på en Nibe S1255 (SE3) i oktober 2026.

## Krav

| | |
|---|---|
| Värmepump | Nibe **S-serien** (S1155/S1255/S2125 + VVM …) ansluten med kärnintegrationen **[Nibe Heat Pump](https://www.home-assistant.io/integrations/nibe_heatpump/)** (Modbus). Varmvattenläget ska ha alternativen `SMALL / MEDIUM / LARGE`. |
| Priser | En Nord Pool-sensor med attributen `raw_today` / `raw_tomorrow` – HACS-integrationen **[Nordpool](https://github.com/custom-components/nordpool)**. Både kvarts- och timpriser fungerar. |
| Recorder | Används för att läsa in historik du redan har, så att provperioden börjar med data. |
| Home Assistant | 2025.1 eller senare. |

F-serien har andra register och lägesnamn; entiteterna kan väljas för hand men det är inte testat än.

## Installation

**HACS (eget repository):** HACS → ⋮ → *Anpassade repositorier* → lägg till
`https://github.com/thundrM/nibe-price-optimizer`, typ *Integration* → installera → starta om.

**Manuellt:** kopiera `custom_components/nibe_price_optimizer` till `/config/custom_components/` och starta om.

Sedan *Inställningar → Enheter och tjänster → Lägg till integration → Nibe Price Optimizer*.

## Inställning

1. Välj Nibe-värmepumpen och Nord Pool-prissensorn.
2. **Nätkostnad per kWh** – allt du betalar per kWh *utöver* spotpriset i prissensorn:
   elöverföring, energiskatt och elbolagets påslag (inkl. moms om prissensorn har moms). Den här
   siffran avgör mycket: med 1 kr/kWh ovanpå blir en extra kWh aldrig billig, inte ens när
   spotpriset är noll.
3. Provperiodens längd (dagar).
4. Entiteterna hittas automatiskt via Modbus-register (t.ex. `…-40057` = varmvattenläge).
   Kontrollera och fyll i det som saknas.

Alternativ (senare, via *Konfigurera*): nätkostnad, provperiod, timme då morgondagens priser
publiceras, grundförskjutning för värmen, komfortband (± °C), innegivare och mål, och om Nibes
egen *Smart Price Adaption* ska stängas av när värmestyrningen är aktiv.

## Det här får du

| Entitet | |
|---|---|
| `sensor.…_provperiod` | Pågår / klar, dygn data, dagar kvar |
| Rekommendation varmvatten / värme | Bästa strategi och besparing per dygn/månad |
| Varmvattenbeslut / beslut värmeförskjutning | Vad vald strategi vill just nu, och varför |
| Varmvatten-COP / värme-COP | Uppmätt COP + inlärd modell (attribut) |
| En sensor per strategi | Dess skuggbeslut just nu + resultat (kostnad/dygn, kWh/dygn, laddningar, andel LARGE) |
| Varmvattenstrategi / värmestrategi (select) | Strategin som används vid skarp styrning |
| Varmvattenstyrning aktiv / värmestyrning aktiv (switch) | **Av från början** |
| Knappar | Skapa rapport · Använd rekommendationen · Starta om provperioden |

När provperioden är klar får du en notis, och rapporten skrivs till
`/config/nibe_price_optimizer/report_latest.md` (på svenska om HA är på svenska).

## Varmvattenstrategier

Nibe laddar varmvatten när BT6 sjunker under lägets starttemperatur och slutar vid
stopptemperaturen (SMALL 40→44, MEDIUM 44→48, LARGE 48→51 som standard – läses från din pump).
Strategierna väljer bara läget.

| Strategi | Idé |
|---|---|
| Alltid SMALL / MEDIUM / LARGE | Jämförelser. *Alltid MEDIUM* är grunden som besparingen räknas mot. |
| Priskvartiler | LARGE när priset är bland de billigaste 25 % kommande 24 h och ≥ 0,50 under de dyra; SMALL när dyrt; hysteres. |
| LARGE bara ≤ X | LARGE bara under ett absolut spotpris. |
| **Prognos-LARGE** | Undvik LARGE. Förutse när Nibe skulle ladda nästa gång i MEDIUM (med din inlärda förbrukningsprofil). LARGE bara om den laddningen skulle bli ≥ X dyrare än nu **och** den extra värmen räcker förbi den. Två varianter av "räcker förbi": nästa laddning ska kosta ≤ nu + 0,30 (strikt), eller vara ≥ X billigare än den undvikna. Bara kända priser används – innan morgondagens priser publicerats gissar den aldrig. |
| … utan SMALL | Samma, men aldrig SMALL. |

## Värmestrategier

Prisförskjutning av värmekurvans förskjutning runt din grundförskjutning: förvärm (+1/+2) när
det är billigt nu och tydligt dyrare inom 18 h, sänk (−1/−2) när det är dyrt jämfört med de
billiga timmarna runt omkring. Ett komfortband hindrar att (den uppskattade) innetemperaturen
flyttas mer än ± X °C.

## Så görs utvärderingen

* **Samma tappning för alla strategier.** BT6-fallet som verkligen hände (duschar, stillastående)
  spelas upp genom varje strategi, så att skillnader i förbrukning mellan dagar inte avgör –
  det är därför 14 dagar räcker. (Mäter man direkt varierar varmvattnet ±1 kWh/dygn, vilket
  döljer skillnader mellan styrningar i flera veckor.)
* **Ärliga priser.** En strategi ser bara de priser som var kända vid tillfället.
* **Inlärd modell:** kWh värme per °C på BT6, laddtakt, temperaturberoende förlust,
  förbrukningsprofil per timme och **COP per BT6-temperaturband** (ur Nibes energilogg).
  COP-banden betyder mest: på en S1255 var varmvatten-COP ≈3,7 under 44 °C, ≈3,0 vid 44–48 °C
  och ≈2,6 vid 48–51 °C – att lagra värme i LARGE kostar ~30 % mer el per kWh.
* **Kostnad** = el × (spotpris + din nätkostnad). Tankens/husets sluttillstånd värderas, så en
  strategi kan inte "vinna" genom att sluta kallare.
* **Värmen är en uppskattning:** en enkel husmodell (värmeförlust inlärd ur datan,
  värmekapacitet antagen) – se den som riktning, inte exakt belopp.

## Skarp styrning och säkerhet

* Ingenting skrivs när switcharna är av.
* Värden skrivs bara när de ändras (skonar pumpens minne).
* Stänger du av varmvattenstyrningen sätts MEDIUM; stänger du av värmestyrningen sätts
  grundförskjutningen och Smart Price Adaption slås på igen om den var på.
* **Stäng av egna automationer** som skriver samma register (40057 varmvattenläge, 40031
  förskjutning) innan du aktiverar – annars motarbetar de varandra.
* Om Home Assistant stannar behåller pumpen senaste läge/förskjutning.

## Lärdomar från originalinstallationen

* Nibes sensorer "energy used over the past hour" uppdateras ~40 s efter varje heltimme.
  Långtidsstatistikens `max` för dem dubbelräknar föregående timme – använd medelvärdet
  (integrationen läser värdet själv).
* Stora tappningar drar ner BT6 till ~20 °C och Nibe börjar ladda själv. Ingen strategi kan göra
  bättre då, så de tillfällena räknas inte som besparing.

## Utveckling

```bash
pip install pytest-homeassistant-custom-component
pytest
python scripts/generate_translations.py   # efter nya strategier eller fält
```

Beräkningskärnan (`core/`) är ren Python utan Home Assistant och kan köras på exporterad data.

## Licens

MIT
