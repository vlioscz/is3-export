<p align="center"><img src="brands/logo.png" alt="IS3 · vlios.cz" width="360"></p>

# IS3 Export

[![hacs][hacs-badge]][hacs] [![Validate](https://github.com/vlioscz/is3-export/actions/workflows/validate.yaml/badge.svg)](https://github.com/vlioscz/is3-export/actions/workflows/validate.yaml)

[English](README.md) · **Česky**

NEOFFICIÁLNÍ Home Assistant integrace pro **centrální jednotky iNELS** (ELKO EP).
Komunikuje s jednotkou přímo přes **UDP port 9999** — ten samý port, na který se
připojuje konfigurační software jednotky — takže **nepotřebuje Connection
Server**. „IS3" v názvu je **iNELS3**, formát exportu `.is3`, ze kterého
integrace vychází.

**Na jednotce se nemusí nic povolovat.** Každá vyzkoušená jednotka na tom portu
odpovídala tak, jak byla, a změny posílala sama od sebe — není co otevírat za
port ani co zapínat v IDM3.

Seznam zařízení se bere z exportu `.is3`: z jednotek, které ho nabízejí ke
stažení po HTTP, se stáhne rovnou, u ostatních ho uložíš z IDM3 a přetáhneš do
formuláře — **novější jednotky export po HTTP nenabízejí** (ověřeno na
**CU3-08M**), takže tam ho jednou nahraješ.

> **Stav: experimentální.** Než budeš předpokládat, že je tvoje jednotka
> pokrytá, přečti si [Co je otestované](#co-je-otestované): část řady je ověřená
> na živém hardwaru, u části se jen očekává, že se chová stejně. Rolety hlásí
> **odhadovaný stav** — bez zpětné vazby o poloze; viz [Omezení](#omezení).

Jak byly protokoly zjištěny a co tenhle software je a co není:
[NOTICE.md](NOTICE.md).

## Co je otestované

Tahle tabulka je o jediné věci: jestli jde seznam zařízení **naimportovat
automaticky**, nebo se musí uložit z IDM3 a jednou nahrát. Všechno ostatní —
čtení, zápisy, události, topení, stmívače, tlačítka — je ověřené na referenční
instalaci, klasické CU3-0x s IDM3 03-04-19.

| Jednotka | IDM3 | Automatický import |
| --- | --- | --- |
| **CU3-01M**, **CU3-02M** (navzájem totožné) | 03-03-34, 03-04-19 | ✅ vyzkoušeno, funguje |
| **CU3-08M** | 03-05-03 | ❌ vyzkoušeno, nefunguje — export nahraješ |
| **CU3-07M**, **CU3-09M**, **CU3-10M** | — | předpokládá se chování jako u 08M; **nevyzkoušeno** |

Samotný parser exportu stojí na širší základně: exporty z několika instalací,
17 až 1125 položek, zapsané IDM3 03-03-34 až 03-05-03. Pokud provozuješ
jednotku, která v téhle tabulce není, tabulka roste přesně z toho, že řekneš,
jak to dopadlo — tak či onak.

## Aktualizace firmwaru to může změnit

Protokol, kterým tahle integrace mluví, byl zjištěn **pozorováním, ne ze
specifikace**, takže ho aktualizace firmwaru může beze slova změnit.

- Je **ověřený proti jednotkám s IDM3 03-04-19 a 03-05-03**. Ostatní verze
  vyzkoušené nejsou — což není totéž jako rozbité.
- Když aktualizace formát na drátě opravdu změní, klient **jednou a nahlas
  varuje** v logu a pojmenuje, co místo toho dostal.
- `compat_check.py` pusť **před** aktualizací a soubor si nech — otiskne každý
  předpoklad, na kterém integrace stojí, a potom vypíše, který z nich se pohnul
  a co tě ta změna stojí. **Jen čte** a netiskne nic, co by instalaci
  identifikovalo, takže výstup jde bez obav vložit do issue.

```bash
python tools/compat_check.py 192.168.1.10 --save before.json
# ... aktualizace firmwaru jednotky ...
python tools/compat_check.py 192.168.1.10 --compare before.json
```

## Jak stav zůstává aktuální

Jednotka posílá každou změnu, kterou udělá — cvaknutí relé vypínačem na zdi,
novou naměřenou teplotu, stisk tlačítka — aniž by se kdekoliv muselo cokoliv
zaškrtávat. Vlastní příkazy z HA integrace **ověří zpětným čtením**: když se
výstup neuchytil nebo ho mezitím přehodil vypínač na zdi, srovná se stav na
skutečnost místo aby ikona zůstala viset ve špatném stavu. Zápis jednotka
potvrdí za **4 ms** a její vlastní událost o tom zápisu dorazí o **0,13 s**
později.

Kromě událostí se navíc **každá čitelná adresa přečte v každém 30sekundovém
cyklu** — přečtení celé instalace (**313 čitelných adres**) trvá **0,13 s** —
takže adresa, které přestanou chodit události, je do jednoho cyklu zase
v obraze. **Tlačítka se takhle nečtou**, jinak by se přehrál stisk, který
nikdo neudělal.

**Seznam zařízení** sleduje jednotku taky: každý cyklus si integrace vyžádá
otisk projektu nahraného v jednotce a export stáhne znovu jen tehdy, když se ten
otisk změní — tedy když technik republikuje z IDM3.

## Instalace

Je v **HACS default store**: otevři **HACS**, vyhledej **IS3 Export**, dej
**Download**, **restartuj Home Assistant** a přidej integraci. Obě tlačítka
udělají totéž na jedno kliknutí:

[![Přidat repozitář do HACS][hacs-badge-btn]][hacs-add] [![Přidat integraci][config-badge]][config-add]

Ručně: zkopíruj `custom_components/is3_export` do `config/custom_components/`.

## Nastavení

| Pole | Popis | Výchozí |
| --- | --- | --- |
| Adresa | IP adresa jednotky | — |
| Port | UDP. Měň ho jen tehdy, když se k jednotce chodí přes tunel nebo přesměrovaný port. | `9999` |
| Heslo centrální jednotky | heslo nastavené na jednotce v IDM3; **nech prázdné, pokud žádné nastavené není**, což je běžný případ | prázdné |
| Cesta k souboru exportu | nech prázdné, stáhne se z jednotky | prázdné |
| Nahrání exportu | pro jednotky, které export po HTTP nenabízejí — přetáhni sem `.is3` uložený z IDM3; kopie se drží v `config/is3_export/` | — |

Název integrace se vezme z hlavičky exportu. **Heslo je pro jednotku, ne pro
export** — jednotka ho nabízí jako statický soubor bez přihlášení; kdyby ho přesto nějaká jednotka blokovala, nahraj export nebo zadej
cestu k lokálně staženému.

Cokoliv z toho se dá opravit i později, bez mazání integrace: **Nastavení →
Zařízení a služby → IS3 Export → ⋮ → Překonfigurovat**. Stejné menu → **Smazat**
odebere všechny entity a zařízení, které integrace vytvořila; v centrální
jednotce nic nezůstane a mimo `custom_components/is3_export` se nezapisují žádné
soubory.

## Přechod z 0.1.x

0.2.0 vyměňuje přenos: všechno teď jde přes **UDP port 9999**. **Existující
instalace se povýší za chodu** — ID entit, oblasti i historie zůstávají.

- **Port** se přepíše na `9999`, ať tam bylo uložené cokoliv.
- Dvě nastavení připojení, která původní přenos potřeboval, **mizí** a s nimi
  i ta oprava (repair), která na ně upozorňovala.
- Pokud má centrální jednotka v IDM3 nastavené **heslo**, Home Assistant vyvolá
  své obvyklé opětovné ověření a zeptá se na něj. Když žádné nastavené není,
  neptá se na nic.
- **Stojí za to udělat ručně: vypnout v IDM3 *Third part setting*.** To
  nastavení sedí v jednotce, ne v Home Assistantu, takže ho odsud nikdo vypnout
  nemůže — a dokud je zapnuté, drží jednotka otevřené dveře, které nechtějí
  žádné heslo a které tahle integrace už nepoužívá.

**Návrat zpátky na 0.1.x** znamená integraci smazat a přidat znovu (ID entit,
oblasti i historie jdou s ní), takže si napřed udělej zálohu, jestli chceš mít
tuhle cestu otevřenou.

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
| `0x01`**`08`** | analogový vstup (vstup `Light-IN` = osvětlení / lux) | `sensor` | ❌ |
| `0x01`**`03`**, `0x01`**`11`**, `0x01`**`12`** | kanály regulátorů | `sensor` | ❌ |
| `0x02`**`06`** | vodoměry, elektroměry | `sensor` (total) | ❌ |
| `0x05`**`01`**, `0x02`**`04`**, `0x02`**`09`**, `0x0003` | plány, skupiny, rozvrhy | — | ❌ |

Zapisuje se **jen tam, kde je zápis doložený** — nikdy do vstupů,
termostatických kanálů ani plánů. Rozhoduje **hardwarové ID**, ne jméno: co
začíná na `Controller_`, `Heat-Regulator_` nebo `Cool-Regulator_`, je vnitřnost
regulátoru a nezapisuje se do toho, zatímco nepojmenované relé
(`_ SA3-04M_RE2_…`) je pořád relé.

### Názvy zpřesňují typ entity

Adresa říká, čím výstup je; jméno říká, k čemu slouží.

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

Názvy se dělí na `_` a `-`, na velikosti písmen nezáleží a konkrétnější vyhrává
(`imp_sv_chodba` je tlačítko). `sv`, `imp`, `vent`, `zas` a `TL` musí sedět jako
celý token (jinak by `Svod_vody` bylo světlo a `Zastineni` zásuvka), `lamp`,
`zrc` a `LED` stačí jako předpona. Tlačítko `imp` pošle při stisku **puls**,
takže každý další stisk je zase čistá náběžná hrana pro iNELS program.

**`TL_`** (tlačítko) udělá `event` tlačítko na **jakémkoli** modulu. **`DIN`**
vstup je tlačítko na **nástěnných ovladačích** a na **samotné centrální
jednotce** (In-Out); na ostatních modulech (např. vstupní modul `IM3`) zůstává
běžným `binary_sensor` — udržovaným kontaktem — dokud ho nepojmenuješ `TL_`.
Drátová tlačítka rozlišují `press` i `long_press` (viz
[Nástěnné vypínače](#nástěnné-vypínače-wsb3)).

Světelné a spínací konvence platí **jen pro relé a stmívače** a nikdy z ničeho
neudělají zapisovatelnou entitu (vstup pojmenovaný `Sv_okno` zůstane
`binary_sensor`); `TL`/`DIN` platí naopak **jen pro digitální vstupy**. Víc
pravidel záměrně není — když ti něco vyjde jinak, přepiš typ entity nebo ikonu
ručně v Home Assistantu.

### Žaluzie

Skládají se z několika adres do jedné entity `cover`, ze tří možných zdrojů:

1. **Systémové bity programu žaluzií** (`0x0203`) — nahoru, dolů, stop,
   naklápění. Program v jednotce si řídí kontakty a sám žaluzii zastaví.
   Preferováno, když existuje.
2. **Dvojice relé JA3** — směr je v hardwarovém ID (`JA3-018M_Up1` / `Down1`),
   stop uvolněním obou.
3. **Obyčejná dvojice relé** (modul `SA3`, i BOX varianta `SA3-02B`) — dva relé
   na jeden motor, hardwarově blokované. Tady je směr v **názvu**: token
   `UP`/`DOWN` **kdekoliv** v názvu (`Roleta_loznice_UP`, `…_UP_…`) spáruje obě
   poloviny na **stejném modulu**.

Holé relé se samo nerozepne, takže relé roleta (formy 2 i 3) dostane
**travel-time** entitu `number` (výchozí 30 s): po té době integrace relé
rozepne, tak ji **nastav na reálnou dobu chodu** rolety, o chlup víc, ať dojede
na doraz dřív. Reverz nejdřív uvolní opačný směr, chvíli počká a pak zabere.

Dva výstupy, z nichž každý patří **jiné topné zóně**, zůstanou záměrně dvěma
spínači — špatně poskládaná roleta by ovládala skutečná
relé. Poloha se nehlásí, takže cover nese **odhadovaný stav**, a adresy, které
si vezme žaluzie, už nevzniknou jako spínače.

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

Režim **Cool** se nabídne **jen u zóny, která má reálně zapojený chladicí
výstup** — chladicí kanály nese *každá* zóna, takže jejich přítomnost sama
o sobě nic neznamená. Nastavení teploty přepne zónu do **Manualu**; hodnoty
předvoleb 1–4 i týdenní plán za Schedule se nastavují v jednotce, ne tady.

Každá zóna má navíc `select` **plán** — Běžný / Prázdninový / Sváteční. Sváteční
je **denní** program a musí být v jednotce nakonfigurovaný; kde není, přepnutí
se neuchytí a zpětné čtení plán v UI srovná zpět.

### Nástěnné vypínače (WSB3)

Jeden vypínač se rozpadne na entitu za každý kanál:

| Typ | Rozpad na entity |
| --- | --- |
| **WSB3-20** | 8 — 2 tlačítka (nahoru/dolů) + 2 LED (zelená/červená) + 2 teploty + 2 dig. vstupy |
| **WSB3-40** | 12 — 4 tlačítka + 4 LED + 2 teploty + 2 dig. vstupy |
| **WSB3-*-Hum** | +2 — vlhkost (`%`, `device_class humidity`) a rosný bod (°C) |

Indikační **LED** (role `Green`/`Red`) jsou spínače s ikonou **G**/**R** —
pozná se to z role, takže i nepojmenované je dostanou.

Tlačítka (Up/Down/DIN) jsou **`event` entity**. Drátová rozlišují krátký
**`press`** od **`long_press`** — držení se počítá jako dlouhé po **1,5 s**,
stejně jako v iNELS, a událost vystřelí v tom okamžiku, nečeká na puštění. To
platí pro celou drátovou rodinu nástěnných ovladačů: `GSB3`, `GSP3`, `MSB3`,
`GBP3`, `GRT3`, čtečky karet `GMR3`/`GCR3`/`GHR3`/`GCH3`, informační panely
`GDB3`, `WMR3` a pokojový regulátor `IDRT3`. Short/long stojí na tom, že
události dorazí, když se stanou, takže cokoliv, co je zdrží, dobu držení
rozmaže — viz
[Další programy komunikující s jednotkou](#další-programy-komunikující-s-jednotkou).

**RF ovladače hlásí jen `press`** a **držené RF tlačítko ho vystřelí víckrát** —
viz [RF zařízení](#rf-zařízení). Výjimkou je **`IBWL`** (RF vstupní modul):
každý jeho vstup zrcadlí spárované RF zařízení — tlačítko, ale i dveřní nebo
pohybové čidlo, což z exportu nepoznáme — takže zůstává `binary_sensor`, dokud
ho nepojmenuješ `TL_`. Čidlo přiblížení a čtečka karty se jako tlačítka neberou.

### Zařízení a co je skryté

Každý **fyzický modul** (podle sériového čísla v hardwarovém ID) je v HA
**vlastní zařízení** vnořené pod centrální jednotku, takže kanály jednoho
vypínače nebo relé desky drží pohromadě. Systémové věci (bity, integery,
tlačítka) modul nemají a zůstávají přímo na centrální jednotce.

Velké instalace exportují stovky vnitřností panelů — kontakty tlačítek,
indikační LEDky, poruchové příznaky. Entity z nich vzniknou, ale jsou **ve
výchozím stavu vypnuté**; zapneš je v nastavení integrace. Nepojmenované dostanou
název z role v hardwarovém ID (např. `Up`, `Green`). Vypnuté jsou — a to **i
když jsou pojmenované** — také **`SW` stavové vstupy** relé a **poruchové/alert
příznaky** (`OUF-Alert`, `0x0107`, `device_class problem`, diagnostický).

### RF zařízení

Zařízení na RF modulu (např. dálkový ovladač `RFKEY`) je vlastní zařízení. Jeho
tlačítka jsou entity `event` a hlásí jen `press`; stav baterie `Battery_LOW` je
`binary_sensor` s `device_class battery`.

> **Držené RF tlačítko vystřelí `press` víckrát.** Modul během držení posílá
> „sepnuto" zhruba každých 1,5 s a každé z toho je odsud k nerozeznání od
> stisku — dvousekundové podržení na živém `RFKEY` udělalo dva. Drátové
> tlačítko tohle nedělá. Pokud automatizace na RF tlačítku nesmí proběhnout
> dvakrát, dej jí pojistku (`mode: single` s `max_exceeded: silent`, nebo
> podmínku na čas posledního spuštění).

### Co je v exportu a hodnoty

Export **není** seznam všeho — v IDM3 se vybírá, co se do něj zahrne. Chybí-li
ti něco v Home Assistantu, přidej to tam a republikuj: integrace si příští cyklus
všimne a sama se přenačte (viz
[Jak stav zůstává aktuální](#jak-stav-zůstává-aktuální)); **Reload** to udělá
hned.

Teploty a vlhkosti chodí **vynásobené stem** — 2550 znamená 25,50 °C. Stmívače
jsou rovnou v procentech. `SYSTEMINTEGER` je **syrová hodnota**; co znamená,
určuje program, který ji používá. **Počítadla** (`0x0206`) hlásí skutečné stavy
jednotky.

## ⚠️ Bezpečnost

**Autorizace jednotky není skutečná překážka.** Ve výchozím stavu přijme
**prázdné heslo**, a tak je většina jednotek nechaná — takže kdokoliv, kdo na
jednotku v síti dosáhne, může ovládat celou instalaci. Heslo nastavené na
jednotce v IDM3 laťku zvedne; šifrované na tom není nic tak jako tak. Drž
jednotku v oddělené VLAN nebo ji aspoň odděl firewallem od nedůvěryhodných
zařízení a od internetu.

## Omezení

- **Rolety nehlásí polohu.** Cover ukazuje odhadovaný stav
  (otevřeno / zavřeno / v pohybu), ne procenta; doraz relé rolety se odvozuje
  z nastavené doby chodu, neměří se.
- **Scény se nedají spouštět** — čtení na nich nevrátí žádnou hodnotu, zápis neověřený.
- **Binární formáty `.otc` / `.cld` se nečtou.** Obsahují navíc pojmenované scény.
- **Šifrované není nic** — ani HTTP export, ani provoz na portu 9999.

## Další programy komunikující s jednotkou

**Naměřené:** **konfigurační software jednotky připojený ve stejnou chvíli stojí
skoro nic** — jedno čtení trvalo mediánově **4 ms** a celá instalace **179 ms**
(proti 130 ms bez něj), zápisy se potvrzovaly za **8 ms** a proud událostí běžel
celou dobu. Home Assistant tedy může klidně běžet, zatímco pracuješ na projektu.

**Neměřené:** jak se to chová vedle běžícího **iNELS Connection Serveru**.
Očekává se, že v pohodě, ale nikdo to nezkoušel — pokud ho provozuješ, založ
prosím issue s tím, co pozoruješ.

## Diagnostika

- Senzor **Stav jednotky** (diagnostický) na centrální jednotce: *Běží*,
  *Běží (rychlý režim)*, nebo **Zastavená**. Zastavená jednotka pořád odpovídá po
  síti a drží poslední hodnoty, takže bez tohohle v Home Assistantu všechno
  vypadá normálně, zatímco v domě nereaguje nic. Atribut `unit_clock` je
  **vlastní** čas jednotky — podle něj běží topné rozvrhy, takže rozdíl o hodinu
  (jednotka zůstala na zimním čase) spíná topení v jinou hodinu, než čekáš.
- **Stáhni diagnostiku** z menu **⋮** integrace: redigovaný snímek konfigurace,
  schopností jednotky a každé položky s živou hodnotou a tím, jak se
  klasifikovala. Host i případné přihlašovací údaje jsou začerněné, takže je to
  nejrychlejší věc k přiložení k bug reportu.
- **`tools/probe_is3.py <ip>`** odpoví na totéž zvenčí, když se integrace vůbec
  nenastaví — jestli jednotka odpovídá, jestli chce heslo, jestli se otevře
  datová rovina a jestli posílá události. Vypisuje jen adresy a hodnoty, nikdy
  názvy zařízení; bez přepínače `--write` jen čte.
- **`tools/compat_check.py`** než sáhneš jednotce na firmware — viz
  [Aktualizace firmwaru to může změnit](#aktualizace-firmwaru-to-může-změnit).

## Vývoj

```bash
pip install -r requirements-test.txt
pytest
```

Na Windows si nejdřív přečti [CONTRIBUTING.md](CONTRIBUTING.md) — ušetří
odpoledne (Python 3.13, krátká cesta k venv a trik s wheelem `lru-dict`).

Druhá cesta do jednotky je **XML-RPC** na iNELS Connection Serveru (port 7801),
kterou tahle integrace nepoužívá; pro tu existuje
[InelsForHass](https://github.com/JH-Soft-Technology/InelsForHass).

## Licence

[MIT](LICENSE)

[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Default-41BDF5.svg

<!-- My Home Assistant redirects: resolve against the reader's own instance. -->
[hacs-add]: https://my.home-assistant.io/redirect/hacs_repository/?owner=vlioscz&repository=is3-export&category=integration
[hacs-badge-btn]: https://my.home-assistant.io/badges/hacs_repository.svg
[config-add]: https://my.home-assistant.io/redirect/config_flow_start/?domain=is3_export
[config-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
