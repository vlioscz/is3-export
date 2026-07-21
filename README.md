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
| `vent` | `switch` | ventilátor |

`sv`, `imp` a `vent` musí sedět jako celý token (jinak by `Svod_vody` bylo
světlo), `lamp` a `zrc` stačí jako předpona (protože zrcadlo se píše `zrc`
i `zrcadlo`).

Tlačítko (`imp`) při stisku pošle **puls** — bit na `1` a hned zpět na `0`.
Klidový stav je vždy `0`, takže každý další stisk je zase čistá náběžná hrana,
na kterou iNELS program zareaguje. (Držet `1` by zabralo jen jednou, jednotka
si bit sama nenuluje.)
Dělí se na `_` a `-`, na velikosti písmen nezáleží. Konkrétnější vyhrává:
`imp_sv_chodba` je tlačítko.

Konvence platí **jen pro fyzická relé** a nikdy z ničeho neudělají zapisovatelnou
entitu — vstup pojmenovaný `Sv_okno` zůstane `binary_sensor`, systémový bit
`blok_noc_lamp` zůstane spínačem.

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
| požadovaná teplota | `Required-Therm-AOUT` |
| topí / chladí | `Required-Heat-DOUT` / `Required-Cool-DOUT` |
| předvolba | `Control-Manual-IN` — 0 Schedule, 1–4 Preset 1–4, **7 Manual** |
| zapnuto / vypnuto | `Control-IN` — 0 vyp, 1 zap |

Nastavení teploty přepne zónu do Manualu a zapíše `Manual-Therm-AIN`. Hodnoty
předvoleb 1–4 i týdenní plán za Schedule (`HEATCOOL_WEEK`) se nastavují
v jednotce.

Pozor na jedno úskalí (ošetřené): zápis setpointu **hned** po přepnutí do
Manualu ho zkorumpuje — hodnota spadne pod mrazovou ochranu (~0,1 °C) a s ní
i topné relé, zóna přestane topit. Proto integrace po přepnutí **počká**, pak
setpoint zapíše a **ověří zpětným čtením** `Required-Therm-AOUT`, případně zápis
zopakuje. Manual je hodnota **7**, ne 5 — pětka shodí zónu na mrazovou ochranu.

Každá zóna má navíc `select` **plán** — Běžný / Prázdninový (`Control-Plan-IN`
0 / 64, ověřeno). Třetí plán (sváteční, nejspíš 128) chybí — na testované
jednotce nebyl nakonfigurovaný, takže ho nešlo ověřit; doplní se, až bude na čem.

### Nepojmenované položky jsou vypnuté

Velké instalace exportují stovky vnitřností panelů — kontakty tlačítek,
indikační LEDky, poruchové příznaky. Entity z nich vzniknou, ale jsou
**ve výchozím stavu vypnuté**. Zapneš je v nastavení integrace.

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
