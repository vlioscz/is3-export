<p align="center"><img src="brands/logo.png" alt="IS3 · vlios.cz" width="360"></p>

# IS3 Export

[![hacs][hacs-badge]][hacs] [![Validate](https://github.com/vlioscz/is3-export/actions/workflows/validate.yaml/badge.svg)](https://github.com/vlioscz/is3-export/actions/workflows/validate.yaml)

NEOFFICIÁLNÍ Home Assistant integrace pro **centrální jednotky iNELS** (ELKO EP) přes jejich
ASCII rozhraní — především starší **CU3-01M** a **CU3-02M**. Komunikuje s
jednotkou přímo, **nepotřebuje Connection Server**.

> „IS3" v názvu je **iNELS3** — formát exportu `.is3`, ze kterého integrace
> vychází.

Seznam zařízení si stáhne z exportu, který jednotka sama servíruje. Stav
sleduje živě: jednotka posílá změny sama, takže se nic nepolluje.

> **Stav: experimentální.** Protokol je ověřený proti živé jednotce, parser
> proti exportům ze sedmi instalací (17 až 1125 položek, IDM3 03-03-34 až
> 03-05-03). Neověřené zůstávají žaluzie na reálném pohonu — viz
> [Omezení](#omezení).

## ❗ Nejdřív povol protokol v IDM3

Bez tohohle nefunguje nic — jednotka na ASCII portu neposlouchá.

V **iNELS IDM3** → *Konfigurace centrální jednotky* → **Third part setting**:

| Položka | Co s ní |
| --- | --- |
| **Port** | Zvol volný port. Žádný výchozí neexistuje, poznamenej si ho. |
| **Oddělovač** | Musí souhlasit s nastavením integrace. `[32]` je mezera. |
| **Číselná soustava** | Musí souhlasit s nastavením integrace. |
| **Režim** | Vzdálenné ovládání + IDM |

Napravo zaškrtej **události, které má jednotka posílat**. Co nezaškrtneš, to se
integrace nikdy nedozví a entita zůstane viset na poslední hodnotě:

- `Digital_OUT_SwitchOn` / `SwitchOff` — relé (bez nich nepoznáš cvaknutí vypínačem)
- `Analog_OUT_ValueChanged`, `Analog_OUT_SwitchOn` / `SwitchOff` — stmívače
- `Analog_IN_ValueChange`, `Sensor_Change` — teploty, vlhkosti, analogové vstupy
- `Digital_IN_SwitchOn` / `SwitchOff` — vstupy a tlačítka
- `SysInt_Change`, `Program_ValueSwitchOn` / `Off` — systémové proměnné

Nakonec **Uložit do CU**.

Na těchhle zaškrtávátkách závisí, jak rychle se stav objeví v Home Assistantu.
Jednotku ovládají i vypínače na zdech, takže změna nemusí přijít z HA — a pozná
ji jen z události. **Co má vlastní událost, aktualizuje se řádově do sekundy;
co ne, až při pravidelném dočítání (30 s).** To kolísání u změn ze zdi
(hned vs. 2–3 s) je zpoždění samotné jednotky, než změnu na ASCII odešle, ne
integrace.

Vlastní příkazy z HA se zobrazí okamžitě a integrace je pak **ověří zpětným
čtením** — když se výstup neuchytil nebo ho mezitím přehodil vypínač na zdi,
oprav se stav na skutečnost místo aby ikona zůstala viset ve špatném stavu.

## Instalace

[![Přidat repozitář do HACS][hacs-badge-btn]][hacs-add]

Pak **Download**, restart Home Assistanta a:

[![Přidat integraci][config-badge]][config-add]

Ručně: zkopíruj `custom_components/is3_export` do `config/custom_components/`.

## Nastavení

| Pole | Popis | Výchozí |
| --- | --- | --- |
| Host | IP adresa jednotky | — |
| ASCII port | **z IDM3** | `22272` |
| Export file path | nech prázdné, stáhne se z jednotky | prázdné |
| Oddělovač | **z IDM3**, nabízí všech 27 možností | mezera `[32]` |
| Číselná soustava | **z IDM3** | hexadecimální |

Název integrace se vezme z hlavičky exportu.

**Heslo se nezadává.** Export servíruje webserver jednotky jako statický soubor
bez přihlášení, takže **heslo projektu iNELS na jeho dostupnost nemá vliv**.
(Kdyby přesto nějaká jednotka stažení blokovala, zadej cestu k lokálně
staženému exportu.)

Když entity hlásí *odhadovaný stav*, sedí ti špatně **oddělovač**.

## Které adresy se stanou entitami

Druhý bajt adresy určuje typ:

| Adresa | Význam | Entita | Zápis |
| --- | --- | --- | --- |
| `0x01`**`02`** | relé | `switch` | ✅ |
| `0x01`**`04`** | stmívač (s jednotkou `%`) | `light` 0–100 % | ✅ |
| `0x02`**`03`** | SYSTEMBIT | `switch` | ✅ |
| `0x02`**`02`** | SYSTEMINTEGER | `number` | ✅ |
| dvojice adres | žaluzie | `cover` | ✅ |
| kanály regulátoru | topná zóna | `climate` | ✅ |
| `0x01`**`01`** | vstupy, tlačítka, stavové výstupy regulátoru | `binary_sensor` | ❌ |
| `0x01`**`07`** | poruchy modulů | `binary_sensor` (problem) | ❌ |
| `0x01`**`05`** | teplota / vlhkost | `sensor` | ❌ |
| `0x01`**`08`** | analogový vstup | `sensor` | ❌ |
| `0x01`**`03`**, `0x01`**`11`**, `0x01`**`12`** | kanály regulátorů | `sensor` | ❌ |
| `0x02`**`06`** | vodoměry, elektroměry | `sensor` (total) | ❌ |
| `0x05`**`01`**, `0x02`**`04`**, `0x02`**`09`**, `0x0003` | plány, skupiny, rozvrhy | — | ❌ |

Zapisuje se **jen tam, kde je zápis doložený**. Do vstupů, termostatických
kanálů ani plánů nikdy.

Rozhoduje **hardwarové ID**, ne jméno: co začíná na `Controller_`, je vnitřnost
regulátoru a nezapisuje se do toho — okenní čidlo sedí ve stejném rozsahu jako
relé. Naopak nepojmenované relé (`_ SA3-04M_RE2_…`) je pořád relé.

### Názvy zpřesňují typ entity

Adresa říká, čím výstup je; jméno říká, k čemu slouží. Impulz a lampa jsou
z pohledu adresy totéž.

| V názvu | Entita | Ikona |
| --- | --- | --- |
| `imp` | `button` | — |
| `sv` | `light` | žárovka |
| `lamp` | `light` | stojací lampa |
| `zrc` | `light` | zrcadlo |
| `LED` | `light` | LED pásek |
| `vent` | `switch` | ventilátor |
| `zas` | `switch` | zásuvka |
| `TL` (nebo `DIN` vstup) | `event` (`press` + `long_press`) | — |

`sv`, `imp`, `vent`, `zas` a `TL` musí sedět jako celý token (jinak by
`Svod_vody` bylo světlo a `Zastineni` zásuvka), `lamp`, `zrc` a `LED` stačí jako
předpona.

**`TL_`** (tlačítko) udělá `event` tlačítko na **jakémkoli** modulu. **`DIN`**
vstup je tlačítko na **nástěnných ovladačích** a na **samotné centrální
jednotce** (In-Out); na ostatních modulech (např. vstupní modul `IM3`) je `DIN`
běžný `binary_sensor` (udržovaný kontakt), dokud ho nepojmenuješ `TL_`. Drátové
tlačítko rozlišuje `press` i `long_press` (viz
[Nástěnné vypínače](#nástěnné-vypínače-wsb)).

Tlačítko (`imp`) při stisku pošle **puls** — bit na `1` a hned zpět na `0`.
Klidový stav je vždy `0`, takže každý další stisk je zase čistá náběžná hrana,
na kterou iNELS program zareaguje. (Držet `1` by zabralo jen jednou, jednotka
si bit sama nenuluje.)
Dělí se na `_` a `-`, na velikosti písmen nezáleží. Konkrétnější vyhrává:
`imp_sv_chodba` je tlačítko.

Světelné a spínací konvence (`sv`, `lamp`, `zrc`, `LED`, `vent`, `imp`) platí
**jen pro fyzická relé/stmívače** a nikdy z ničeho neudělají zapisovatelnou
entitu — vstup pojmenovaný `Sv_okno` zůstane `binary_sensor`, systémový bit
`blok_noc_lamp` zůstane spínačem. Naopak `TL`/`DIN` platí **jen pro digitální
vstupy** (z relé tlačítko neudělají).

Víc pravidel záměrně není. Když ti něco vyjde jinak, přepiš typ entity nebo
ikonu ručně v Home Assistantu.

### Žaluzie

Skládají se z několika adres do jedné entity `cover`, ze dvou možných zdrojů:

1. **Systémové bity programu žaluzií** — nahoru, dolů, stop, naklápění. Program
   v jednotce si řídí kontakty sám. Má přednost.
2. **Dvojice relé JA3** — jen nahoru a dolů, stop uvolněním obou. Použije se,
   jen když program v exportu není.

Adresy, které si vezme žaluzie, už nevzniknou jako spínače.

### Topné zóny

Regulátor topení je sada kanálů se stejnou sériovou příponou plus pojmenovaný
kořen `<název> Controller_<sériové>`. Z nich vznikne jedna entita `climate`:

| | Kanál |
| --- | --- |
| aktuální teplota | `Actual-Therm-AOUT` |
| požadovaná teplota | `Required-Therm-AOUT` (topení) / `Required-Cool-Therm-AOUT` (chlazení) |
| topí / chladí | `Required-Heat-DOUT` / `Required-Cool-DOUT` |
| předvolba | `Control-Manual-IN` — 0 Schedule, 1–4 Preset 1–4, **7 Manual** |
| topení / chlazení | `Control-HC-IN` — 0 topení, 1 chlazení |
| zapnuto / vypnuto | `Control-IN` — 0 vyp, 1 zap |

Přes `Control-HC-IN` se zóna přepíná mezi režimy **Heat** a **Cool** (kde je
chladicí výstup zapojený). Chlazení má vlastní setpointy: `Required-Cool-Therm-AOUT`
(v platnosti) a `Manual-Cool-Therm-AIN` (manuální).

Nastavení teploty přepne zónu do Manualu a zapíše `Manual-Therm-AIN` (topení),
resp. `Manual-Cool-Therm-AIN` (chlazení). Hodnoty předvoleb 1–4 i týdenní plán
za Schedule (`HEATCOOL_WEEK`) se nastavují v jednotce.

Pozor na jedno úskalí (ošetřené): zápis setpointu **hned** po přepnutí do
Manualu ho zkorumpuje — hodnota spadne pod mrazovou ochranu (~0,1 °C) a s ní
i topné relé, zóna přestane topit. Proto integrace po přepnutí **počká**, pak
setpoint zapíše a **ověří zpětným čtením**, případně zápis zopakuje. Manual je
hodnota **7**, ne 5 — pětka shodí zónu na mrazovou ochranu.

Každá zóna má navíc `select` **plán** — Běžný / Prázdninový / Sváteční
(`Control-Plan-IN` 0 / 64 / 128, vše ověřeno na živé jednotce). Sváteční je
**denní** program (`HEATCOOL_DAY`) a musí být v jednotce nakonfigurovaný; kde
není, přepnutí se neuchytí a zpětné čtení plán v UI srovná zpět.

### Nástěnné vypínače (WSB3)

Jeden vypínač se rozpadne na entitu za každý kanál — nic se nespeciálně-neřeší,
vyplyne to z typu adresy:

| Typ | Rozpad na entity |
| --- | --- |
| **WSB3-20** | 8 — 2 tlačítka (nahoru/dolů) + 2 LED (zelená/červená) + 2 teploty + 2 dig. vstupy |
| **WSB3-40** | 12 — 4 tlačítka + 4 LED + 2 teploty + 2 dig. vstupy |
| **WSB3-*-Hum** | +2 — vlhkost (`%`, `device_class humidity`) a rosný bod (°C) |

Indikační **LED** (role `Green`/`Red`) jsou spínače s ikonou **G**/**R** —
pozná se to z role, takže i nepojmenované (`_`) je dostanou.

Tlačítka (Up/Down/DIN) jsou **`event` entita**. Drátové vypínače (WSB) rozlišují
**krátký `press` a `long_press`**; tlačítka **RF ovladače** hlásí jen `press`.

Totéž rozpoznání platí pro **celou rodinu nástěnných ovladačů** — kromě `WSB3` i
skleněné/dotykové `GSB3`, `GSP3`, `MSB3`, `GBP3`, `GRT3`, čtečky karet
`GMR3`/`GCR3`/`GHR3`/`GCH3`, informační panely `GDB3`, `WMR3` a pokojový
regulátor `IDRT3` (všechny drátové → `press`+`long_press`). **RFKEY** dálkový
ovladač je celý tlačítka (jen `press`). **`IBWL`** (RF vstupní modul) je jiný —
každý jeho vstup zrcadlí spárované RF zařízení (tlačítko, ale i dveřní/pohybové
čidlo), což z exportu nepoznáme, takže je defaultně `binary_sensor`; ať je z
konkrétního vstupu `press`, pojmenuj ho `TL_`. Čidlo přiblížení a čtečka karty
se jako tlačítka neberou.

**Jak short/long funguje:** rozlišení potřebuje dobu držení = mezeru mezi
sepnutím (`=1`) a rozepnutím (`=0`). Na drátovém vypínači je tahle mezera čistá a
konzistentní — ťuknutí padnou pod ~100 ms, záměrná držení nad ~1,5 s, s širokou
prázdnou mezerou mezi tím. Integrace proto na sepnutí spustí časovač: přijde-li
dřív rozepnutí, je to krátký `press`; když časovač (**1,5 s**, stejně jako
long-press v iNELS) doběhne a tlačítko je **pořád držené**, je to `long_press` —
vystřelí **hned v tom okamžiku, nečeká na puštění**, takže akce na dlouhý stisk
naskočí včas. Ztracené rozepnutí tlačítko nezasekne — pojistný časovač uvolní.

> ⚠️ **Podmínka spolehlivého short/long: neběžící Connection Server.** Jeho
> periodický sběr jednotku na pár sekund zmrazí a dobu držení rozmaže (viz sekci
> **Connection Server zpomaluje odezvu** níže). Původně to kvůli tomu vypadalo
> jako slepá ulička; v čistých podmínkách je timing spolehlivý.

**RF ovladače zůstávají jen na `press`** — jejich rozepnutí se ztrácí příliš
často, doba držení tam spolehlivá není. `press` se u nich vystřelí na **každou
událost sepnutí**; tlačítka se přitom **nededupují** (integrace normálně probudí
entitu jen při změně hodnoty), aby ztracené rozepnutí neschovalo další stisk —
jinak by byl další stisk „beze změny" a zahodil by se (odtud dřívější „musím
3×"). Krátký debounce (~0,5 s) spolkne jen okamžité dvojposlání téhož stisku.

> Senzory se naopak **utlumují** (max ~1 notifikace/s), aby ukecaný analogový
> vstup CU nezahltil smyčku — hodnota se ukládá dál, jen se stav nezapisuje
> pořád. Tím zůstává zpracování tlačítek svižné.

Stav baterie RF ovladače je běžný `binary_sensor` (battery), ne tlačítko.

### Rozdělení na zařízení

Každý **fyzický modul** (podle sériového čísla v hardwarovém ID) je v HA
**vlastní zařízení** vnořené pod centrální jednotku. Kanály jednoho vypínače,
relé desky nebo stmívače tak drží pohromadě — poznáš, který `Green1` patří ke
kterému vypínači. Systémové věci (bity, integery, tlačítka) modul nemají a
zůstávají přímo na centrální jednotce.

### Skryté ve výchozím stavu

Velké instalace exportují stovky vnitřností panelů — kontakty tlačítek,
indikační LEDky, poruchové příznaky. Entity z nich vzniknou, ale jsou
**ve výchozím stavu vypnuté**. Zapneš je v nastavení integrace. Nepojmenované
dostanou název z role v hardwarovém ID (např. `Up`, `Green`), ne z celého ID.

Vypnuté jsou i **`SW` stavové vstupy** relé a **poruchové/alert příznaky**
(`OUF-Alert`, typ `0x0107`) — a to **i když jsou pojmenované**, protože je
sleduje málokdo. Alert má `device_class problem` a je diagnostický.

### RF zařízení

Zařízení na RF modulu (např. `RFKEY` — dálkové ovladače) se objeví jako vlastní
zařízení s tlačítky (`binary_sensor`) a stav baterie `Battery_LOW` dostane
`device_class battery`.

### Co je v exportu

Export **není** seznam všeho — v IDM3 se vybírá, co se do něj zahrne. Chybí-li
ti něco v Home Assistantu, přidej to tam. Integrace kontroluje jednou za
30 minut, jestli se seznam změnil, a sama se přenačte. Hned to udělá **Reload**.

### Hodnoty

Teploty a vlhkosti chodí **vynásobené stem** — 2550 znamená 25,50 °C. Stmívače
jsou rovnou v procentech. `SYSTEMINTEGER` je **syrová hodnota**, která se nijak
nepřepočítává; co znamená, určuje program, který ji používá.

## ⚠️ Bezpečnost

**ASCII port nemá žádnou autentizaci** — a heslo na jednotce to nezmění, to
chrání jen webserver. Kdokoliv, kdo se dostane na ten TCP port, může ovládat
celou instalaci.

Drž jednotku v oddělené VLAN nebo ji aspoň odděl firewallem od nedůvěryhodných
zařízení a od internetu.

## Omezení

- **Žaluzie nejsou ověřené na reálném pohonu.** U relé varianty se předpokládá,
  že `1` motor rozjede a `0` zastaví; pauza při obracení chodu je odhad.
- **Scény se nedají spouštět** — `GET` na ně vrací `N`, zápis neověřený.
- **Binární formáty `.otc` / `.cld` se nečtou.** Obsahují navíc pojmenované scény.
- **HTTP i ASCII port jdou bez šifrování.**

## ⚠️ Connection Server zpomaluje odezvu

Pokud tutéž centrální jednotku obsluhuje i **iNELS Connection Server**, počítej
s občasnou prodlevou. Connection Server si zhruba **každých 40–60 s** sáhne na
jednotku pro kompletní stav a jednotka při tom na **~2–4 s zamrzne celý ASCII
výstup** — přestane posílat události i vykonávat příkazy. Do tohoto okna tu a tam
spadne stisk nebo přepnutí, které pak reaguje o ty 2–4 s později — a to **pro
všechny klienty naráz**, tedy i pro samotný Connection Server (zpožďuje tak i
sám sebe).

- **Nepotřebuješ-li Connection Server, vypni ho** — odezva integrace je pak
  plynulá (jednotka odpovídá ~180 ms).
- **Potřebuješ-li ho**, zpomal/odlehči v jeho konfiguraci ten periodický sběr
  stavů (jak často a kolik toho z jednotky čte).

Ověřeno izolačně: integrace sama žádné zamrzání nezpůsobuje — vzniká jen tehdy,
když je připojený i Connection Server.

> Nesouvisí s tím ani počet klientů: centrální jednotka má **omezený počet ASCII
> spojení**. Nenechávej na port `22272` mířit spoustu klientů naráz — když se
> sloty vyčerpají, jednotka spojení sice přijme, ale přestane obsluhovat (HTTP
> export jede dál) a pomůže až restart CU.

## Diagnostika

Když něco nesedí, tenhle skript zjistí, co jednotka umí — je read-only, dokud
nepřidáš `--write`:

```bash
python tools/probe_is3.py <ip> <port> 0x0102000A
```

## Vývoj

```bash
pip install -r requirements-test.txt
pytest
```

Jednotka mluví i jinými protokoly, se kterými integrace nepracuje: **ELKONET**
(binární, port 9999) a **XML-RPC** na Connection Serveru (port 7801) — pro tu
cestu existuje [InelsForHass](https://github.com/JH-Soft-Technology/InelsForHass).

## Licence

[MIT](LICENSE)

[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg

<!-- My Home Assistant redirects: these resolve against whatever instance the
     reader is signed in to, so no address of anyone's Home Assistant appears
     here. -->
[hacs-add]: https://my.home-assistant.io/redirect/hacs_repository/?owner=vlioscz&repository=is3-export&category=integration
[hacs-badge-btn]: https://my.home-assistant.io/badges/hacs_repository.svg
[config-add]: https://my.home-assistant.io/redirect/config_flow_start/?domain=is3_export
[config-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
