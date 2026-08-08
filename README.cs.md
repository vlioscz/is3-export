<p align="center"><img src="brands/logo.png" alt="IS3 · vlios.cz" width="360"></p>

# IS3 Export

[![hacs][hacs-badge]][hacs] [![Validate](https://github.com/vlioscz/is3-export/actions/workflows/validate.yaml/badge.svg)](https://github.com/vlioscz/is3-export/actions/workflows/validate.yaml)

[English](README.md) · **Česky**

NEOFFICIÁLNÍ Home Assistant integrace pro **centrální jednotky iNELS** (ELKO EP).
Komunikuje s jednotkou přímo přes **UDP port 9999** — **ten samý port**, na
který se připojuje konfigurační software jednotky — takže **nepotřebuje
Connection Server**.

> „IS3" v názvu je **iNELS3** — formát exportu `.is3`, ze kterého integrace
> vychází.

**Na jednotce se nemusí nic povolovat.** Na každé vyzkoušené jednotce na tom
portu odpovídala tak, jak byla, a změny posílala sama od sebe bez jakéhokoliv
nastavování událostí — není co otevírat za port ani co zapínat v IDM3.

Seznam zařízení se bere z exportu `.is3`: z jednotek, které ho servírují **přes
HTTP**, se stáhne rovnou, u ostatních ho uložíš z IDM3 a přetáhneš do formuláře —
**novější jednotky export přes HTTP neservírují** (ověřeno na **CU3-07M** a
**CU3-08M**), takže tam ho jednou nahraješ. Stav se pak sleduje živě z vlastních
událostí jednotky a k tomu se ještě jednou za 30 s přečte všechno znovu.

> **Stav: experimentální.** Než budeš předpokládat, že je tvoje jednotka
> pokrytá, přečti si [Co je otestované](#co-je-otestované): část řady je ověřená
> na živém hardwaru, u části se jen očekává, že se chová stejně. Rolety hlásí
> **odhadovaný stav** — bez zpětné vazby o poloze; viz [Omezení](#omezení).

Jak byly protokoly zjištěny a co tenhle software je a co není:
[NOTICE.md](NOTICE.md).

## Co je otestované

| Jednotka | Co bylo doopravdy vyzkoušené |
| --- | --- |
| **CU3-01M** (nejstarší generace) | ověřené **čtení** |
| klasická **CU3-0x** — referenční instalace, IDM3 **03-04-19** | **všechno**: čtení, zápisy, události, topení, stmívače, tlačítka |
| **CU3-07M**, IDM3 **03-05-03** | ověřené **čtení i zápis** |
| **CU3-08M** | jen to, co potřebovala 0.1.x: **neservíruje export přes HTTP** a nikdy neotevřela port, který 0.1.x používala. **Její port 9999 vyzkoušený není.** |
| **CU3-09M**, **CU3-10M** | **nevyzkoušené vůbec.** Předpokládá se, že se chovají jako 07M/08M, protože jde o stejnou rodinu firmwaru — ale to je očekávání, ne výsledek. |

Parser exportu je jiná věc a stojí na širší základně: exporty z několika
instalací, 17 až 1125 položek, zapsané IDM3 **03-03-34** až **03-05-03**.

Pokud provozuješ jednotku, která v téhle tabulce není, nejužitečnější věc, co
můžeš udělat, je říct, jak to dopadlo — tak či onak. Tabulka roste přesně z toho.

## Aktualizace firmwaru to může změnit

Protokol, kterým tahle integrace mluví, byl zjištěn **pozorováním, ne ze
specifikace**. Nic na něm není slíbené, že zůstane, jak je, a aktualizace
firmwaru jednotky ho může beze slova změnit.

- Je **ověřený proti jednotkám s IDM3 03-04-19 a 03-05-03**. Ostatní verze
  vyzkoušené nejsou — což není totéž jako rozbité.
- Když aktualizace formát na drátě opravdu změní, integrace může přestat
  fungovat. Log to řekne: klient **jednou a nahlas varuje**, když jednotka
  pošle datagramy, jejichž úvodní bajty nezná, a pojmenuje, co místo toho
  dostal.
- Přesně na tuhle otázku existuje nástroj, **nový v tomhle vydání**:

```bash
python tools/compat_check.py 192.168.1.10 --save before.json
# ... aktualizace firmwaru jednotky ...
python tools/compat_check.py 192.168.1.10 --compare before.json
```

`compat_check.py` otiskne každý předpoklad, na kterém integrace stojí —
hlavičku paketu, kontrolní součet, tvar každé odpovědi, kódování hodnot, formát
exportu i vlastní tabulku verzí protokolu v jednotce — a vypíše, který z nich se
pohnul, každý s jednou srozumitelnou větou o tom, co tě ta změna stojí. **Jen
čte** a netiskne nic, co by instalaci identifikovalo (žádné názvy zařízení,
žádný název projektu), takže výstup jde bez obav vložit do issue.

Pusť ho **před** aktualizací firmwaru a soubor si nech. Předtím má
nesrovnatelně větší cenu než potom.

## Jak stav zůstává aktuální

Jednotka posílá každou změnu, kterou udělá — cvaknutí relé vypínačem na zdi,
novou naměřenou teplotu, stisk tlačítka — aniž by se kdekoliv muselo cokoliv
zaškrtávat.

Vlastní příkazy z HA se zobrazí okamžitě a integrace je pak **ověří zpětným
čtením**: když se výstup neuchytil nebo ho mezitím přehodil vypínač na zdi,
srovná se stav na skutečnost místo aby ikona zůstala viset ve špatném stavu.
Naměřeno na autorově jednotce: zápis jednotka potvrdí za **4 ms** a její vlastní
událost o tom zápisu dorazí o **0,13 s** později.

**Seznam zařízení** sleduje jednotku taky. Každý cyklus si integrace vyžádá
otisk projektu nahraného v jednotce — jeden paket — a export stáhne znovu jen
tehdy, když se ten otisk změní, což je přesně okamžik, kdy technik republikuje
z IDM3. Republikovaný projekt se tak projeví do jednoho cyklu místo do půl
hodiny a po zbytek času se nestahuje vůbec nic.

Kromě událostí integrace navíc **přečte každou čitelnou adresu v každém
30sekundovém cyklu**. Může si to dovolit: přečtení celé instalace — **313
čitelných adres** — trvá **0,13 s**. Adresa, které přestanou chodit události,
tak nezůstane viset; do jednoho cyklu je zase v obraze, aniž by cokoliv muselo
předem vědět, které adresy jsou ohrožené.

**Tlačítka se do toho znovupřečtení neberou.** Tlačítko nemá stav, který by
stálo za to obnovovat — je to okamžik, ne hodnota — a znovupřečtení by jen
riskovalo přehrání stisku, který nikdo neudělal.

## Instalace

Je v **HACS default store**: otevři **HACS**, vyhledej **IS3 Export** a dej
**Download** — nebo použij tohle tlačítko na jedno kliknutí:

[![Přidat repozitář do HACS][hacs-badge-btn]][hacs-add]

Pak **restartuj Home Assistant** a přidej integraci:

[![Přidat integraci][config-badge]][config-add]

Ručně: zkopíruj `custom_components/is3_export` do `config/custom_components/`.

## Nastavení

| Pole | Popis | Výchozí |
| --- | --- | --- |
| Adresa | IP adresa jednotky | — |
| Port | UDP. Měň ho jen tehdy, když se k jednotce chodí přes tunel nebo přesměrovaný port. | `9999` |
| Heslo centrální jednotky | heslo nastavené na jednotce v IDM3; **nech prázdné, pokud žádné nastavené není**, což je běžný případ | prázdné |
| Cesta k souboru exportu | nech prázdné, stáhne se z jednotky | prázdné |
| Nahrání exportu | pro jednotky, které export přes HTTP neservírují — přetáhni sem `.is3` uložený z IDM3; kopie se drží v `config/is3_export/` | — |

Název integrace se vezme z hlavičky exportu.

**Heslo je pro jednotku, ne pro export.** Export servíruje webserver jednotky
jako statický soubor bez přihlášení, takže **heslo projektu iNELS na jeho
dostupnost nemá vliv**. (Kdyby přesto nějaká jednotka stažení blokovala, nahraj
export nebo zadej cestu k lokálně staženému.)

Cokoliv z toho se dá opravit i později, bez mazání integrace: **Nastavení →
Zařízení a služby → IS3 Export → ⋮ → Překonfigurovat**.

## Přechod z 0.1.x

0.2.0 vyměňuje přenos: všechno teď jde přes **UDP port 9999**. **Existující
instalace se povýší za chodu** — ID entit, oblasti i historie zůstávají a nic se
nemusí nastavovat znovu.

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

Přečtení celé instalace — **313 čitelných adres** — trvá **0,13 s** a adresy
počítadel (`0x0206` — vodoměry a elektroměry) hlásí skutečné stavy jednotky.

**Návrat zpátky na 0.1.x** znamená integraci smazat a přidat znovu (ID entit,
oblasti i historie jdou s ní), takže si napřed udělej zálohu, jestli chceš mít
tuhle cestu otevřenou.

## Odebrání integrace

**Nastavení → Zařízení a služby → IS3 Export → ⋮ → Smazat.** Tím se zavře
spojení a odeberou se všechny entity a zařízení, které integrace vytvořila.
V centrální jednotce nic nezůstane — integrace s ní jen komunikovala po síti —
a mimo `custom_components/is3_export` se nezapisují žádné soubory.

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

Zapisuje se **jen tam, kde je zápis doložený**. Do vstupů, termostatických
kanálů ani plánů nikdy.

Rozhoduje **hardwarové ID**, ne jméno: co začíná na `Controller_`,
`Heat-Regulator_` nebo `Cool-Regulator_`, je vnitřnost regulátoru a nezapisuje
se do toho — okenní čidlo sedí ve stejném rozsahu jako relé. Naopak
nepojmenované relé (`_ SA3-04M_RE2_…`) je pořád relé.

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
[Nástěnné vypínače](#nástěnné-vypínače-wsb3)).

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

Skládají se z několika adres do jedné entity `cover`, ze tří možných zdrojů:

1. **Systémové bity programu žaluzií** (`0x0203`) — nahoru, dolů, stop,
   naklápění. Program v jednotce si řídí kontakty a sám žaluzii zastaví.
   Preferováno, když existuje.
2. **Dvojice relé JA3** — směr je v hardwarovém ID (`JA3-018M_Up1` / `Down1`),
   stop uvolněním obou.
3. **Obyčejná dvojice relé** (modul `SA3`, i BOX varianta `SA3-02B`) — dva relé
   na jeden motor, hardwarově blokované. Tady je směr v **názvu**, ne v
   hardwarovém ID: token `UP`/`DOWN` **kdekoliv** v názvu — jako přípona
   (`Roleta_loznice_UP`) i uprostřed (`…_UP_…`) — spáruje obě poloviny na
   **stejném modulu**.

Holé relé se samo nerozepne, takže **relé roleta (formy 2 i 3 — JA3 pár
funguje úplně stejně, jen má blokování směrů zadrátované na desce modulu)**
dostane **travel-time** entitu `number` (výchozí 30 s): po pohybu integrace
relé po té době rozepne, tak ji **nastav na reálnou dobu chodu** rolety
(o chlup víc), ať dojede na doraz dřív. Reverz nejdřív uvolní opačný směr,
chvíli počká a pak zabere — modul směry hardwarově blokuje.

Jméno je slabý důkaz, takže jedno spárování se záměrně odmítá: **dva výstupy,
z nichž každý patří jiné topné zóně, zůstanou dvěma spínači**, nespojí se do
žaluzie. Zóna nahoře a zóna dole nesou `up` a `down` v názvu každá z vlastního
důvodu a špatně poskládaná roleta by ovládala skutečná relé — jedné místnosti by
topení zapnula a druhé vypnula.

Poloha se nehlásí, takže cover nese **odhadovaný stav**. Adresy, které si vezme
žaluzie, už nevzniknou jako spínače.

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

Režim **Cool** se u zóny nabídne **jen když má reálně zapojený chladicí výstup**.
Chladicí kanály (`Control-HC-IN`, `Required-Cool-*`) totiž nese *každá* zóna, takže
jejich přítomnost nestačí — schopnost pozná až kořenový řádek regulátoru: topná
zóna má flags `0x05` s prázdnými chladicími sloty plánu, zóna s chlazením `0x3F`
s vyplněnými (ověřeno na jednotce). Kde je Cool k dispozici, přepíná se přes
`Control-HC-IN` a chlazení má vlastní setpointy: `Required-Cool-Therm-AOUT`
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

Short/long stojí na tom, že události dorazí, když se stanou, takže cokoliv, co
je zdrží, dobu držení rozmaže — viz
[Další programy komunikující s jednotkou](#další-programy-komunikující-s-jednotkou).

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
zařízení. Jeho tlačítka jsou entity `event` se stiskem `press` (RF nehlásí
držení, takže žádný `long_press`).

> **Držené RF tlačítko vystřelí `press` víckrát.** Modul během držení posílá
> „sepnuto" zhruba každých 1,5 s a každé z toho je odsud k nerozeznání od
> stisku — dvousekundové podržení na živém `RFKEY` udělalo dva. Drátové
> tlačítko tohle nedělá. Pokud automatizace na RF tlačítku nesmí proběhnout
> dvakrát, dej jí pojistku (`mode: single` s `max_exceeded: silent`, nebo
> podmínku na čas posledního spuštění).

Stav baterie `Battery_LOW` je `binary_sensor` s `device_class battery`. Výjimkou je vstup **`IBWL`**: zrcadlí to, co je s ním
spárované — tlačítko i dveřní kontakt, což z exportu nepoznáme — takže zůstává
`binary_sensor`, dokud ho nepojmenuješ `TL_`.

### Co je v exportu

Export **není** seznam všeho — v IDM3 se vybírá, co se do něj zahrne. Chybí-li
ti něco v Home Assistantu, přidej to tam a republikuj: integrace si příští cyklus
všimne, že se projekt změnil, a sama se přenačte (viz
[Jak stav zůstává aktuální](#jak-stav-zůstává-aktuální)). Hned to udělá
**Reload**.

### Hodnoty

Teploty a vlhkosti chodí **vynásobené stem** — 2550 znamená 25,50 °C. Stmívače
jsou rovnou v procentech. `SYSTEMINTEGER` je **syrová hodnota**, která se nijak
nepřepočítává; co znamená, určuje program, který ji používá. **Počítadla**
(`0x0206`) hlásí skutečné stavy jednotky.

## ⚠️ Bezpečnost

**Autorizace jednotky není skutečná překážka.** Ve výchozím stavu přijme
**prázdné heslo**, a tak je většina jednotek nechaná — takže kdokoliv, kdo na
jednotku v síti dosáhne, může ovládat celou instalaci. Heslo nastavené na
jednotce v IDM3 laťku zvedne; šifrované na tom není nic tak jako tak.

Drž jednotku v oddělené VLAN nebo ji aspoň odděl firewallem od nedůvěryhodných
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
skoro nic.** S ním připojeným trvalo jedno čtení mediánově **4 ms** a celá
instalace **179 ms** (proti 130 ms bez něj), zápisy se potvrzovaly za **8 ms**
a proud událostí běžel celou dobu. Home Assistant tedy může klidně běžet,
zatímco pracuješ na projektu.

**Neměřené:** jak se to chová vedle běžícího **iNELS Connection Serveru**.
Očekává se, že v pohodě — Connection Server chodí na jednotku po tomhle samém
portu pro svůj vlastní provoz a konfigurační klient na tomhle portu prokazatelně
žádné potíže nedělá — ale nikdo to nezměřil. Pokud ho provozuješ, založ prosím
issue s tím, co pozoruješ; přesně tohle měření zatím nikdo nemá.

## Diagnostika

Centrální jednotka dostane senzor **Stav jednotky** (diagnostický): jestli se
hlásí jako *běží*, *běží v rychlém režimu*, nebo je **zastavená**. Zastavená
jednotka pořád odpovídá po síti a pořád drží poslední hodnoty, takže bez tohohle
v Home Assistantu všechno vypadá normálně, zatímco v domě nereaguje nic.

Atribut `unit_clock` jsou **vlastní** datum a čas jednotky. Jednotka si podle
nich řídí topné rozvrhy, takže když se rozcházejí se skutečným časem — typicky
přesně o hodinu, když jednotka zůstala na zimním čase — topení spíná v jinou
hodinu, než čekáš, a nic jiného v Home Assistantu ti neřekne proč.

**Stáhni diagnostiku** z integrace (menu **⋮**) — redigovaný snímek: konfigurace,
schopnosti jednotky a každá položka s živou hodnotou a tím, jak se klasifikovala.
Nejrychlejší věc k přiložení k bug reportu; host a případné přihlašovací údaje
jsou začerněné.

Když se integrace vůbec nenastaví, není z čeho diagnostiku stahovat. Na stejné
otázky odpoví zvenčí `tools/probe_is3.py` — jestli jednotka odpovídá, jestli
chce heslo, jestli se otevře datová rovina a jestli posílá události:

```bash
python tools/probe_is3.py 192.168.1.10
```

Vypisuje jen adresy a hodnoty, nikdy názvy zařízení, takže výstup jde bez obav
vložit do issue. Bez přepínače `--write` jen čte.

A než sáhneš jednotce na firmware, `tools/compat_check.py` zaznamená, na čem
integrace stojí, abys potom poznal, co se pohnulo — viz
[Aktualizace firmwaru to může změnit](#aktualizace-firmwaru-to-může-změnit).

## Vývoj

```bash
pip install -r requirements-test.txt
pytest
```

Na Windows si nejdřív přečti [CONTRIBUTING.md](CONTRIBUTING.md) — ušetří
odpoledne (Python 3.13, krátká cesta k venv a trik s wheelem `lru-dict`).

Integrace komunikuje s jednotkou na **UDP portu 9999**, tedy tom, který
používá její konfigurační software. Druhá cesta do jednotky je **XML-RPC** na iNELS Connection Serveru
(port 7801), kterou tahle integrace nepoužívá; pro tu cestu existuje
[InelsForHass](https://github.com/JH-Soft-Technology/InelsForHass).

Jak byly protokoly zjištěny a jaká omezení z toho plynou:
[NOTICE.md](NOTICE.md).

## Licence

[MIT](LICENSE)

[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Default-41BDF5.svg

<!-- My Home Assistant redirects: these resolve against whatever instance the
     reader is signed in to, so no address of anyone's Home Assistant appears
     here. -->
[hacs-add]: https://my.home-assistant.io/redirect/hacs_repository/?owner=vlioscz&repository=is3-export&category=integration
[hacs-badge-btn]: https://my.home-assistant.io/badges/hacs_repository.svg
[config-add]: https://my.home-assistant.io/redirect/config_flow_start/?domain=is3_export
[config-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
