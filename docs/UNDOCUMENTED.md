# Undocumented KiCad behaviour — a standing log

Behaviours of KiCad 10.0.5 that the official documentation **omits, contradicts, or gets
backwards**. This is the log the owner asked for: *"keep a log of important behaviors that
are missing from docs."*

It exists because this project keeps discovering, case by case, that the docs cannot be
used to define "done". Findings were scattered across agent reports and research files;
this is the permanent home.

---

## Read this first

> ### The official file-format documentation is stamped **2024-11** and still claims to cover "all versions of KiCad from 6.0". That claim is false.
>
> Verified against the live pages on 2026-08-03:
>
> | page | "Last Modified" | opening claim |
> |---|---|---|
> | [`sexpr-intro`](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/) | **2024-11-04** | (no coverage sentence) |
> | [`sexpr-pcb`](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/) | **2024-11-29** | "This documents the s-expression board file format **for all versions of KiCad from 6.0**." |
> | [`sexpr-schematic`](https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/) | **2024-11-29** | "…schematic file format for all versions of KiCad from 6.0." |
> | [`sexpr-symbol-lib`](https://dev-docs.kicad.org/en/file-formats/sexpr-symbol-lib/) | **2024-11-29** | "…symbol library file format for all versions of KiCad from 6.0." |
> | [`sexpr-footprint`](https://dev-docs.kicad.org/en/file-formats/sexpr-footprint/) | **2024-11-29** | "…footprint library file format for all versions of KiCad from 6.0." |
>
> Those stamps predate KiCad 8.0.6. Every page below documents at least one construct that
> 10.0.5 **rejects** (UD-03), one that it **silently discards** (UD-09, UD-21), and one
> whose stated meaning is **the opposite** of what the binary does (UD-11). A parser
> written from these pages will not read a file KiCad 10 writes, and a file written from
> these pages will not load.
>
> **Practical rule for this repo: never author a fixture from the docs alone. Check the
> binary first.** That rule is why this file exists.

### How to read an entry

Each entry is classified, because the three kinds need different upstream action:

| Badge | Meaning | Action |
|---|---|---|
| **[BUG]** | KiCad behaves wrongly or unsafely. | File upstream. |
| **[DOC]** | KiCad is right; the documentation is missing, stale, or contradicts the binary. | Documentation PR / issue. |
| **[UNDOC-OK]** | Behaviour is correct and deliberate but written down nowhere. | Documentation PR; also a conformance-case candidate, since nothing else pins it. |

Every entry gives the **claim**, the **evidence** (command + observed output), what the
**docs say**, and **why it matters**.

### Method, and what "verified" means here

Every behavioural claim below was **re-run against the release oracle**
`kicad/kicad:10.0.5` (`Version: 10.0.5, release build`, build date `Jul 28 2026`,
Debian 13) on **2026-08-03**, in a container, before publication:

```bash
docker run --rm -v "$PWD:/work" -w /work kicad/kicad:10.0.5 bash -c '<command>'
```

Documentation quotes were re-fetched from the live pages on the same date. Source-code
line references were read from the pinned build tree
(`18fb9289ff0efdca53c0352ed81a0973f0a6b58c`).

**Ten previously-circulated claims did not survive that re-check.** They are listed in
§7 with what is actually true, rather than deleted — a retracted claim that leaves no
trace gets rediscovered and re-believed. Anything not independently re-verified is marked
**(unverified)** inline; that marking applies only to bulk token inventories (UD-12,
UD-18, UD-22) and one documentation quotation (UD-11), never to a behavioural claim.

---

## 1. Board format — `.kicad_pcb`

### UD-01 — KiCad 10 never *writes* net ordinals, but still *reads* them, inconsistently per element **[DOC]**

**Claim.** The docs' net model — a required top-level nets section plus `(net NET_NUMBER)`
references — describes nothing 10.0.5 writes. On read the ordinal path still exists, and
behaves **differently on four element types**, one of which is a hard rejection.

**Docs say** ([`sexpr-pcb`](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/), a
section titled *Nets Section*, listed as **required**):

```
(net
  ORDINAL
  "NET_NAME"
)
```

and for connectivity items: *"The `net` token defines by the net ordinal number which net
in the net section that the segment is part of."*

**Evidence — what 10.0.5 writes.** Upgrading a shipped demo
(`pcb upgrade --force /usr/share/kicad/demos/pic_programmer/pic_programmer.kicad_pcb`):

```
grep -c '^\t(net '   -> 0        # no top-level nets section at all
grep -c '(net_name'  -> 0
zones written as:  (zone (net "GND") (layer "B.Cu") …)
```

**Evidence — what 10.0.5 reads.** Per-element matrix, each a minimal hand-authored board
probed with `kicad-cli pcb export stats --format json`:

| element | `(net 1)` (bare ordinal) | `(net 1 "NAME")` | `(net "NAME")` | `(net_name "NAME")` |
|---|---|---|---|---|
| `segment` | **accepted** — resolved through the top-level nets section; an undefined ordinal silently becomes net 0 | n/a | accepted | **rejected** |
| `via` | **accepted**, same resolution | n/a | accepted | **rejected** |
| `zone` | **accepted and silently discarded** — the zone is written back with no `(net …)` at all | n/a | accepted | **accepted**, rewritten as `(net "…")` |
| `pad` | **rejected** | accepted (ordinal ignored, name wins) | accepted | **rejected** |

The one rejection message, and the only place `Expecting net name` occurs:

```
$ kicad-cli pcb export stats --format json -o /tmp/s.json c1c_pad_net_num_only.kicad_pcb
exit=3
Failed to load board: Expecting net name. Got '')'' in '…', line 31, offset 10.
```

Resolution is genuinely by *number*, not by a name that looks like a number: with
`(net 7 "1")` in the header, a segment's `(net 1)` resolves to `""` while `(net "1")`
resolves to `"1"`. The rejections for `net_name` on non-zone elements enumerate the
accepted token set, which is itself the best available documentation:

```
Failed to load board: Expecting start, end, width, layer, solder_mask_margin, net,
tstamp, uuid or locked. Got ''net_name'' in '…', line 23, offset 4.
```

**Why it matters.** This is the largest single divergence between the format docs and the
binary. A writer built from the docs emits a nets section and bare ordinals; that file
*loads*, quietly, with the wrong nets on its zones and — the moment it contains a pad —
fails outright. A reader built from the docs looks for a nets section that no KiCad-10
file has.

---

### UD-02 — The format version codes 10.0.5 writes are documented nowhere **[DOC]**

**Claim.** Round-tripping through 10.0.5 produces these `(version …)` codes:

| format | code written | also writes |
|---|---|---|
| `.kicad_pcb` | **20260206** | `(generator_version "10.0")` |
| `.kicad_mod` | **20260206** | `(generator_version "10.0")` |
| `.kicad_sch` | **20260306** | `(generator_version "10.0")` |
| `.kicad_sym` | **20251024** | `(generator_version "10.0")` |

**Evidence.**

```
$ kicad-cli pcb upgrade --force old.kicad_pcb     # in: (version 20240108)
Successfully saved board file using the latest format
$ grep version old.kicad_pcb  ->  (version 20260206)

$ kicad-cli fp upgrade fpup.pretty                # in: (version 20240108)
->  (version 20260206)

$ kicad-cli sch upgrade --force s.kicad_sch       # in: (version 20250114)
Successfully saved schematic file using the latest format
->  (version 20260306)

$ kicad-cli sym upgrade --force lib.kicad_sym     # in: (version 20231120)
Saving symbol library in updated format
->  (version 20251024)
```

Corroborated independently: all **223** stock libraries in `/usr/share/kicad/symbols`
carry `(version 20251024)`.

Note `generator_version` is the **two-component series** `"10.0"`, not `"10.0.5"`.

**Docs say.** Only that the version is a date "in YYYYMMDD format". No page states any
actual value, for any release.

**Why it matters.** These four numbers are the only machine-readable statement of which
format a file is. A tool that must decide "is this file newer than I understand" has
nothing to compare against, and the version *ceiling* is enforced (UD-10, UD-13, UD-20).

---

### UD-03 — The documented `suppress_zeros` is not a typo you can ignore: it is **rejected** **[DOC]** + **[BUG]** (the message)

**Claim.** The dimension token is spelled `suppress_zeroes`. The documented spelling makes
the board **fail to load**, with a message that names neither the token nor the file.

**Docs say** ([`sexpr-intro`](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/),
Dimension Format): *"The optional `suppress_zeros` token removes all trailing zeros from
the dimension text."*

**Evidence.**

```
$ grep -rho 'suppress_zero[a-z]*' /usr/share/kicad/demos | sort | uniq -c
     46 suppress_zeroes           # 25 of them inside .kicad_pcb files
      0 suppress_zeros

$ kicad-cli pcb export stats --format json -o /tmp/s.json c3_dim_suppress_zeros.kicad_pcb
exit=3
stdout: (empty)
stderr: Unknown format token: 'symbol'
```

Controls: substituting `wibble_wobble` for the token, or writing `(suppress_zeros no)`,
produces the byte-identical message — it is the generic unknown-token-inside-`(format …)`
path.

**Why it matters.** Two defects in one. The **[DOC]** half: a parser or writer built from
the docs fails on, or produces, every KiCad dimension. The **[BUG]** half: the diagnostic
says `Unknown format token: 'symbol'` — it reports the token's *type* (`symbol`, the s-expr
lexer class) instead of its text, omits the `Failed to load board:` prefix every other
board-load failure carries, and gives no filename, line or offset. A user has no way to
find the offending token.

---

### UD-04 — Layer ordinals were renumbered, are undocumented, and are **ignored on read** **[DOC]** + **[UNDOC-OK]**

**Claim.** KiCad 10 writes a layer numbering the docs never give, and the number in a
`(layers …)` entry is not validated at all — it is discarded and re-derived on write.
Objects reference layers **by name only**.

**Evidence — what 10.0.5 writes** (a 32-copper board with all non-copper and `User.1–45`,
authored with deliberately bogus ordinals ≥ 1000, then upgraded):

```
(0 "F.Cu" signal)  (4 In1.Cu) (6 In2.Cu) … (62 In30.Cu)   [In N = 2 + 2N]   (2 "B.Cu" signal)
(1 F.Mask) (3 B.Mask) (5 F.SilkS) (7 B.SilkS) (9 F.Adhes) (11 B.Adhes) (13 F.Paste) (15 B.Paste)
(17 Dwgs.User) (19 Cmts.User) (21 Eco1.User) (23 Eco2.User) (25 Edge.Cuts) (27 Margin)
(29 B.CrtYd) (31 F.CrtYd) (33 B.Fab) (35 F.Fab)
(39 "User.1") (41 "User.2") … (127 "User.45")             [User.N = 37 + 2N]
```

Copper is written first, then non-copper, then user layers.

**Evidence — the ordinal is ignored.** `(4 "F.Cu" signal)` is written back as
`(0 "F.Cu" signal)`; `(999 "User.1" user)` as `(39 "User.1" user)`; the 1000+ board above
loads clean (exit 0).

**Docs say.** The canonical-layer table gives names and no ordinals; the board example
still shows the pre-6 numbering in which `F.Cu` is 15.

**Why it matters.** [DOC] Anyone deriving the numbering from the docs' example gets a
scheme two major versions stale. [UNDOC-OK] The fact that ordinals are *advisory* is
genuinely useful and written nowhere — it means a writer can emit anything and KiCad will
fix it, and it means a reader must not key on the number.

---

### UD-05 — `User.1`–`User.45` exist; the docs say nine **[DOC]**

**Docs say** ([`sexpr-intro`](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/)):
*"9 optional user definable layers"*, listing `User.1` … `User.9`.

**Evidence.**

```
User.1 … User.45   -> exit 0
User.46            -> exit 3
Failed to load board: Layer 'User.46' in file '…' at line 59 is not in fixed layer hash.
```

`User.46` is rejected whether it appears alongside the other 45 or alone.

**Why it matters.** Five times as many layers as documented. A tool that validates against
the documented set rejects valid boards.

---

### UD-06 — Two undocumented **structural** rules on `(layers …)`: copper first, and an even copper count **[UNDOC-OK]**

**Claim.** The loader counts *leading* copper entries. Copper layers must be listed before
any non-copper layer, and the copper count must be **even and ≥ 2**.

**Evidence.** Copper-must-be-first:

```
	(layers
		(0 "F.Cu" signal)
		(25 "Edge.Cuts" user)
		(2 "B.Cu" signal)
	)
->  exit 3
    Failed to load board: 1 is not a valid layer count in '…', line 14, offset 2.
```

Note the count is **1**, not 2: counting stops at the first non-copper entry, so the
trailing `B.Cu` is never counted. Odd copper count:

```
	(layers
		(0 "F.Cu" signal) (4 "In1.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user)
	)
->  exit 3
    Failed to load board: 3 is not a valid layer count in '…', line 15, offset 2.
```

Controls: F.Cu + In1 + In2 + B.Cu (four copper) loads; a single-copper board also fails
with `1 is not a valid layer count`, so the rule is even **and** ≥ 2.

**Why it matters.** Neither rule appears in any documentation, and the diagnostic explains
neither — a user who interleaves a mask layer is told "1 is not a valid layer count" about
a block that visibly contains three layers. This is a high-value conformance case: the
error message is the only place the rule is stated, so a case pins it.

---

### UD-07 — Renaming a **copper** layer silently discards the name; renaming a fixed non-copper layer is an error **[UNDOC-OK]**

**Claim.** A copper layer's name field is not a name — it is positional. The first copper
entry becomes `F.Cu`, the last `B.Cu`, the middle ones `In1.Cu`…`InN.Cu`, whatever you
wrote. Non-copper fixed slots instead hard-reject an unknown name.

**Evidence.**

```
IN   (0 "MyCopper" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user)
OUT  (0 "F.Cu" signal)     (2 "B.Cu" signal) (25 "Edge.Cuts" user)

IN   (0 "B.Cu" signal) (2 "F.Cu" signal)      # declared back-first
OUT  (0 "F.Cu" signal) (2 "B.Cu" signal)      # silently swapped

$ # a segment that referenced the custom name:
IN   (segment … (layer "MyCopper"))
OUT  (segment … (layer "F.Cu"))
```

versus a fixed non-copper slot:

```
(25 "MyEdge" user)     -> exit 3  Failed to load board: Layer 'MyEdge' … is not in fixed layer hash.
(1 "Bogus.Mask" user)  -> exit 3  Failed to load board: Layer 'Bogus.Mask' … is not in fixed layer hash.
```

The supported rename is the **optional 4th field**, and it round-trips for both kinds:

```
IN  (0 "F.Cu" signal "MyCopper")     OUT  (0 "F.Cu" signal "MyCopper")
IN  (25 "Edge.Cuts" user "MyEdge")   OUT  (25 "Edge.Cuts" user "MyEdge")
```

**Why it matters.** The asymmetry is invisible and the copper case is data loss without a
diagnostic: a board whose copper stack is declared back-to-front is silently reinterpreted,
which changes which physical layer every track is on.

---

### UD-08 — `Rescue` is an accepted layer name that KiCad drops on save **[UNDOC-OK]**

**Evidence.** A board declaring `Rescue` (with any ordinal) loads, exit 0; after
`pcb upgrade --force` the layer is **absent** from the written `(layers …)` block. It is
also absent from the upgraded 32-copper board that had it in its input.

**Docs say.** `Rescue` is not in the canonical-layer table at all.

**Why it matters.** Accept-then-discard is the failure mode hardest to notice: nothing
errors, and the file quietly loses a layer on every round trip.

---

### UD-09 — UUIDs are not validated; malformed ones are silently **replaced** **[DOC]** + **[BUG]**

**Claim.** The documented "Version 4 (random) UUID" constraint is enforced nowhere. Any
string loads. But only a *syntactically parseable* UUID survives a round trip — anything
else is replaced with a freshly generated v4, with no warning.

**Docs say** ([`sexpr-intro`](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/)):
*"The UUID attribute is a Version 4 (random) UUID that should be globally unique."*

**Evidence** (board, `pcb export stats` then `pcb upgrade --force`):

| input | loads | written back as |
|---|---|---|
| `(uuid "not-a-uuid")` | exit 0 | `(uuid "fae499d9-4dbd-4d20-88ca-22c163aa43a6")` — **regenerated** |
| `(uuid "")` | exit 0 | `(uuid "c54c348a-4d11-41aa-a4e2-1c887de94de2")` — **regenerated** |
| `(uuid "11111111-2222-1333-1444-555555555555")` (not v4) | exit 0 | unchanged |
| the same UUID on two objects | exit 0 | unchanged, still duplicated |

The schematic loader does the same, and there the consequence is worse: sheet **instance
paths are matched by UUID**, so a mnemonic UUID containing a non-hex character is
regenerated and instance matching silently breaks.

```
$ kicad-cli sch upgrade --force s.kicad_sch     # in: (uuid "7c000000-0000-4000-8000-0000000000u1")
exit 0, no warning
->  (uuid "bdb954af-3659-4d02-9926-5341afc4f74e")
# an all-hex sibling …-0000000001a1 survives untouched
```

**Why it matters.** [DOC] `ROADMAP.md` M1 lists a `rejects-malformed-uuid` case; **it is
not achievable against this oracle** and should be struck. [BUG] Silent identity
replacement is a data-loss class: any external reference to that UUID is broken with no
diagnostic. It also **affects this repo**: fixtures under `suites/schematic-parse/` use
mnemonic non-hex UUIDs (`…-0000000000sa`, `…-0000000010u1`), which KiCad regenerates —
harmless where identity is not load-bearing, silently wrong where it is.

---

### UD-10 — A board rejects a future `(version …)` on **two different code paths**, with very different messages **[UNDOC-OK]**

**Evidence.**

```
(version 99999999) -> exit 3
  Failed to load board: Cannot interpret date code 99999999 in '…', line 4, offset 27.

(version 20990101) -> exit 3
  Failed to load board: KiCad was unable to open this file because it was created with a
  more recent version than the one you are running.
  To open it you will need to upgrade KiCad to version 10.0 or later (file format dated
  20990101 or later).
```

A *plausible* future date takes the helpful branch; an *implausible* one fails the date
parse first.

**Why it matters.** The second message is exemplary and the docs never mention that this
check exists. See §7 for the widely-repeated but **false** claim that a schematic accepts
what a board rejects — it does not (UD-13).

---

### UD-11 — `pad (drill (offset X Y))` does the **opposite** of the documented meaning **[DOC]**

**Claim.** The offset does not move the hole. **The hole stays at the pad's `(at)`; the
copper pad shape moves.**

**Docs say.** The footprint page documents the offset as displacing the drill relative to
the pad. *(The exact sentence is quoted from the research inventory; the live page's drill
section did not come back in this pass' fetch — the direction of the documented claim is
what matters and is unambiguous there.)*

**Evidence.** One footprint at `(at 50 50)`, one through-hole pad at local `(at 0 0)`,
`(size 3 3)`, `(drill 0.8 (offset 1 0))`, against the identical board with the offset
removed:

```
# holes -- kicad-cli pcb export drill --drill-origin absolute --excellon-units mm
   no offset:  T1C0.800  X50.0Y-50.0
   offset 1 0: T1C0.800  X50.0Y-50.0        <-- UNCHANGED

# copper -- kicad-cli pcb export gerbers --layers F.Cu   (%FSLAX46Y46*% %MOMM*% -> 4.6 mm)
   no offset:  %ADD10C,3.000000*%  X50000000Y-50000000D03*
   offset 1 0: %ADD10C,3.000000*%  X51000000Y-50000000D03*   <-- MOVED +1.000000 mm

# placement -- kicad-cli pcb export pos --format csv --units mm : identical in both
   "","","OffsetDrill",50.000000,-50.000000,0.000000,top
```

**Why it matters.** This is the most dangerous entry in the file. A tool that implements
the documented meaning produces boards whose holes are in the wrong place — a
fabrication-affecting error that no parse check catches, because both files are valid.

---

### UD-12 — Board tokens 10.0.5 writes that no page documents **[DOC]**

**Evidence.** Counted across the 19 shipped demo `.kicad_pcb` files in
`/usr/share/kicad/demos` (`grep -rl "(<token>"`):

| token | demo `.kicad_pcb` files containing it |
|---|---|
| `generator_version`, `embedded_fonts`, `tenting`, `legacy_teardrops`, `unlocked` | **19 / 19** |
| `thermal_bridge_angle` | 6 |
| `generated` (length-tuning meanders) | 4 |
| `covering`, `plugging`, `capping`, `filling`, `duplicate_pad_numbers_are_jumpers`, `render_cache` | 3 |
| `embedded_files`, `teardrops`, `zone_layer_connections` | 2 |
| `component_class`, `is_time_domain` | 1 |
| `jumper_pad_groups` | **0** — reported in the inventory, not present in any shipped demo *(unverified)* |

Sub-tokens reported by the format inventory and **not individually re-verified**:
`teardrops`' `best_length_ratio` / `best_width_ratio` / `max_length` / `max_width` /
`curved_edges` / `filter_ratio` / `allow_two_segments` / `prefer_zone_connections`;
`generated`'s `base_line` / `base_line_coupled` / `corner_radius_percent` / `initial_side`
/ `last_*` / `max_amplitude` / `min_amplitude` / `min_spacing` / `single_sided` /
`tuning_mode` / `target_length` / `target_skew` / `override_custom_rules` / `rounded`;
`target_delay`/`_max`/`_min`; and the `pcbplotparams` additions
`allow_soldermask_bridges_in_footprints`, `plot_black_and_white`, `plotpadnumbers`,
`hidednponfab`, `sketchdnponfab`, `crossoutdnponfab`, `pdf_front_fp_property_popups`,
`pdf_back_fp_property_popups`, `pdf_metadata`, `pdf_single_document`,
`dashed_line_dash_ratio`, `dashed_line_gap_ratio`, `plot_on_all_layers_selection`.

Conversely, these **documented** `pcbplotparams` tokens no longer appear in 10.0.5 output
*(unverified in this pass)*: `svguseinch`, `excludeedgelayer`, `viasonmask`,
`plotreference`, `plotvalue`, `linewidth`, `hpglpenoverlay`, `padsonsilk`, `plotothertext`.

**Why it matters.** Five of these appear in **every** shipped demo board. A reader built
from the docs hits an unknown token on the first real file it opens.

---

### UD-13 — Board and schematic disagree on **diagnostic quality** for the same defect **[BUG]** (schematic side)

**Claim.** Both file types reject a too-new `(version …)`. Only the board says why.

**Evidence.**

```
board,     (version 99999999)  -> exit 3  Failed to load board: Cannot interpret date code 99999999 in '…', line 4, offset 27.
board,     (version 20990101)  -> exit 3  Failed to load board: KiCad was unable to open this file … upgrade KiCad to version 10.0 or later …
schematic, (version 99999999)  -> exit 3  Failed to load schematic
schematic, (version 20990101)  -> exit 3  Failed to load schematic
schematic, (version 20260307)  -> exit 3  Failed to load schematic      # one day past what it writes
schematic, (version 20260306)  -> exit 0                                 # exactly what it writes
symbol lib,(version 20251025)  -> exit 2  Unable to load library         # one day past what it writes
symbol lib,(version 20251024)  -> exit 0
```

**Why it matters.** A user handed a schematic from a newer KiCad gets four words. The
board loader's message tells them the exact remedy. The same information exists on both
paths; only one surfaces it. This is also why `docs/TEST_CASE_FORMAT.md` §7 says a
schematic rejection case must lean on its control — the message carries no information.
Note also the **exit-code inconsistency**: the same class of failure is `3` for a
schematic and `2` for a symbol library.

---

## 2. Schematic format — `.kicad_sch`

### UD-14 — The schematic Y-axis is **flipped** relative to symbol-local Y **[UNDOC-OK]**

**Claim.** For an unrotated, unmirrored instance:
`sheet_x = inst_x + local_x` but **`sheet_y = inst_y − local_y`**.

**Evidence — connectivity derivation.** A library symbol with pin 1 at symbol-local
`(at 0 0 0)` and pin 2 at symbol-local `(at 0 2.54 0)` (both `(length 0)`), placed at
`(at 100 100 0)`, with local labels at sheet Y 97.46 and 102.54:

```
$ kicad-cli sch export netlist --format kicadxml -o y.xml y.kicad_sch
    <net code="2" name="/SHEET_Y_MINUS_2540">
      <node ref="U1" pin="2" …/>          <- symbol-local +2.54 landed at sheet 100 − 2.54
```

and the label at 102.54 caught nothing:
`[label_dangling]: Label not connected @(100.00 mm, 102.54 mm)`.

**Evidence — independent render derivation.** Body rectangle at symbol-local
`(start -1.27 -0.635) (end 1.27 3.175)`; `kicad-cli sch export svg` emits

```
<rect x="98.730000" y="96.825000" width="2.540000" height="3.810000" …/>
```

X: `100 + (−1.27) = 98.73` — not flipped. Y span `96.825 … 100.635` = `100 − 3.175` …
`100 − (−0.635)`. Unflipped would have been `99.365 … 103.175`.

**Why it matters.** A renderer or netlister that places symbol geometry without the sign
flip is wrong on every symbol, and wrong in a way that still produces a plausible-looking
picture. The docs describe both coordinate systems and never state the relation.

---

### UD-15 — Label net naming: **local labels are sheet-scoped too**; only global labels are bare **[UNDOC-OK]**

**Claim.** Hierarchical *and plain local* labels produce path-prefixed net names
(`/sub/NAME`). Global labels are bare. Path-prefixing is therefore **not** what
distinguishes hierarchical from local — connectivity is.

**Evidence.** Root + one subsheet; `U1` on a sheet pin `HSIG`, `U5` on `global_label
GSIG`; in the subsheet `U2` on `hierarchical_label HSIG`, `U3` on `global_label GSIG`,
`U4` on a plain `label LSIG`:

```
$ kicad-cli sch export netlist --format kicadxml -o n.xml root7.kicad_sch
    <net code="1" name="/sub/HSIG">  <node ref="U1" …/>  <node ref="U2" …/>   <- crosses the sheet boundary
    <net code="2" name="/sub/LSIG">  <node ref="U4" …/>                        <- same prefix, never leaves
    <net code="3" name="GSIG">       <node ref="U3" …/>  <node ref="U5" …/>    <- bare
```

A root-sheet local label comes out `/NAME` (root prefix is just `/`).

**Why it matters.** The naming looks like the scoping rule and is not. `/sub/HSIG` and
`/sub/LSIG` are indistinguishable by name while behaving completely differently. Anything
that infers scope from a net name is wrong.

---

### UD-16 — Repeated sheet instances: every instance gets the reference from the **first** `(path …)` entry **[BUG]**

**Claim.** When one subsheet file is instantiated twice, `kicad-cli` emits one `<comp>` per
instance with correct, distinct sheet paths and correct per-instance nets — but assigns
**every** instance the reference stored on the first `(path …)` entry in the symbol's
`(instances)` list, ignoring the rest.

**Evidence.** `shared8.kicad_sch` authors `/…aa → U2` and `/…bb → U3`:

```
$ kicad-cli sch export netlist --format kicadxml -o a.xml root8.kicad_sch
Warning: schematic has annotation errors, please use the schematic editor to fix them
exit=0
    <comp ref="U2"> <sheetpath names="/inst1/" tstamps="/…aa/"/> <tstamps>…c9</tstamps>
    <comp ref="U2"> <sheetpath names="/inst2/" tstamps="/…bb/"/> <tstamps>…c9</tstamps>
```

Nets stay correct per instance (`/inst1/SIG`, `/inst2/SIG`). Two discriminating runs
establish that it is *first-in-file*, not first-by-path and not the
`(property "Reference" …)` value:

- `/…aa → U7` first, `/…bb → U3` second ⇒ both `ref="U7"`
- the same file with the two `(path …)` blocks textually swapped ⇒ both `ref="U3"`

`sch upgrade --force` **preserves both instance paths verbatim** — the file is not damaged;
the loss happens at export time.

**Why it matters.** A BOM or netlist from a design that reuses a sheet is wrong, with only
a generic "annotation errors" warning to go on. See §7: the `.kicad_pro` is **not** the
lever, contrary to the previously-circulated claim.

---

### UD-17 — `bus_alias` is dropped on round-trip and affects no output **[BUG]**

**Claim.** `(bus_alias …)` is parsed, then discarded on save, and it changes nothing in
any output while present.

**Evidence.** Fixture: a bus labelled `MYBUS`, two bus entries to stub wires `A`/`B` into
`U1`/`U2`, plus `(bus_alias "MYBUS" (members "A" "B"))`. Control: the identical file with
only those lines removed (`diff` confirms the sole delta).

```
$ kicad-cli sch upgrade --force rt.kicad_sch
Successfully saved schematic file using the latest format   exit=0
$ grep -c bus_alias rt.kicad_sch  ->  0
```

Netlists from the two files differ only in the file's own path. The ERC reports are
byte-identical, and both show the alias was never honoured:

```
[bus_to_net_conflict]: Invalid connection between bus and net items @(100.00 mm, 70.00 mm): Label 'MYBUS'
[net_not_bus_member]: Net /A is graphically connected to bus /<NO NET> but is not a member of that bus
```

`bus /<NO NET>` **with the alias present** is the tell.

**Why it matters.** Round-trip data loss through a documented token, plus a feature that
silently does nothing in the CLI. Any CLI-driven flow (CI netlist generation, automated
BOM) on a design that uses bus aliases produces wrong connectivity and reports ERC errors
that the GUI would not.

**Now a tracked, tested case.** [DL-0040]'s round-trip write-path testing formalizes this
as `suites/schematic-parse/schematic-bus-alias` (`extra = ["roundtrip"]`,
`known_divergence.answer = "roundtrip"`) and `docs/DIVERGENCES.md`'s DIV-0006 — this is
the finding that motivated that check to include a targeted `bus_alias` census
(`DESIGN.md` §3e) rather than rely on `summary`/`erc` alone, precisely because this entry
already showed neither would ever notice the loss.

---

### UD-18 — Schematic tokens 10.0.5 writes that the page does not document **[DOC]**

**Docs say.** The schematic page documents `polyline` and `text` as the graphic items, and
restricts `fields_autoplaced` to `global_label` and `sheet`.

**Evidence.** Counted across the shipped demo `.kicad_sch` files:

| token | demo files |
|---|---|
| `dnp`, `exclude_from_sim` | 115 |
| `generator_version` | 114 |
| `rectangle` | 108 |
| `embedded_fonts` | 105 |
| `fields_autoplaced` (incl. on ordinary symbols) | 89 |
| `circle` | 77 |
| `mirror` | 66 |
| `arc` | 48 |
| `lib_name` | 33 |
| `text_box` | 26 |
| `image` | 22 |
| `bus_alias` | 19 |
| `rule_area`, `netclass_flag`, `duplicate_pin_numbers_are_jumpers` | 11 |
| `show_name` | 7 |
| `bezier` | 6 |
| `do_not_autoplace` | 5 |
| `in_pos_files` | 2 |
| `table`, `table_cell`, `href` | 1 |

`netclass_flag` was separately verified accepted by 10.0.5 (netlist unchanged, render
changed). `table`'s sub-tokens (`cells`, `cols`, `rows`, `column_count`, `column_widths`,
`row_heights`, `span`, `margins`, `separators`, `border`, `header`) and `netclass_flag`'s
(`length`, `shape`) are inventory-sourced *(unverified individually)*.

**Why it matters.** Seven of these appear in a majority of shipped demos. `table`/
`text_box`/`image`/`rule_area` are whole item classes the page omits.

---

## 3. Symbol and footprint libraries

### UD-19 — Symbol property `(id N)` no longer exists, and is **silently discarded** on read **[DOC]**

**Docs say.** A symbol property is `(property "KEY" "VALUE" (id N) (at …) (effects …))`,
and *"The `id` token defines an integer ID … and must be unique."*

**Evidence.**

```
$ grep -rn "(id " /usr/share/kicad/symbols/       ->  0 matches across all 223 libraries
$ kicad-cli sym upgrade --force c2_id.kicad_sym   # every property carries (id 0..4)
Saving symbol library in updated format           exit=0, stderr empty
$ grep -c "(id " c2_id.kicad_sym  ->  0           # dropped
```

Duplicate ids (`Reference (id 0)` and `Value (id 0)`) also load, exit 0, stderr empty — the
uniqueness rule is unenforced because there is nothing left to enforce. The same holds for
`(id N)` inside a `.kicad_sch` symbol property.

**Why it matters.** The docs specify a required, uniqueness-constrained field that the
current format does not have. A writer emitting it loses it silently; a reader keying on
it finds nothing.

---

### UD-20 — `(pin_numbers hide)` is now `(pin_numbers (hide yes))` **[DOC]**

**Docs say.** The flat form `(pin_numbers hide)`.

**Evidence.** Across the 223 stock libraries: **413** `(pin_numbers` blocks, every one
containing a nested `(hide yes)`; **zero** occurrences of the flat form. (413 blocks
against 22 784 symbols — the block is **omitted entirely** when pin numbers are visible,
which the docs also do not say.)

The flat form still loads and is migrated:

```
$ kicad-cli sym upgrade --force c3_flat.kicad_sym    # in: (pin_numbers hide)
Saving symbol library in updated format   exit=0
-> 		(pin_numbers
   			(hide yes)
   		)
```

Control: the same file with the block deleted round-trips with **no** `pin_numbers` block,
proving the flat token was genuinely parsed as *hide*, not merely tolerated.

---

### UD-21 — KiCad writes **five** symbol properties unconditionally; **none** is required on read **[DOC]**

**Docs say.** Four mandatory symbol properties: Reference, Value, Footprint, Datasheet.

**Evidence.** All **22 784** top-level symbols in the 223 stock libraries carry all five
(Reference, Value, Footprint, Datasheet **and Description**) — zero files with a mismatch.
And a symbol with **no properties at all** round-trips to exactly five synthesized ones:

```
$ kicad-cli sym upgrade --force c4_none.kicad_sym
Saving symbol library in updated format   exit=0, stderr empty (no warning)
->  (property "Reference" "")  (property "Value" "")  (property "Footprint" "")
    (property "Datasheet" "")  (property "Description" "")
```

**Precise statement.** Description is mandatory *in the writer*, not in the reader — and so
are the other four. A file omitting any of them loads cleanly and silently, and gets them
back with empty values on the next save. See §7: "de-facto mandatory fifth property" is
right about the writer and wrong about the reader.

---

### UD-22 — Symbol and footprint tokens the pages do not document **[DOC]**

**Evidence.** Present in all **223 / 223** stock symbol libraries: `show_name`,
`do_not_autoplace`, `exclude_from_sim`, `in_pos_files`,
`duplicate_pin_numbers_are_jumpers`, `embedded_fonts`, `generator_version`. Present in
some: pin `(alternate …)` (49 libraries), `body_styles` (4).

Footprint-level tokens reported by the inventory *(unverified in this pass)*:
`(property "Sheetname" …)`, `(property "Sheetfile" …)`,
`(duplicate_pad_numbers_are_jumpers …)`, `(embedded_fonts …)`, `(component_class …)`,
`(jumper_pad_groups …)`, `(zone_layer_connections …)`, and bare `(sheetname)`/`(sheetfile)`.
Also: `zone_connect` value **3** ("through-hole thermal, SMD solid") is described in the
*zone* section of the docs but omitted from the *footprint* and *pad* `zone_connect` lists,
which stop at 2.

---

## 4. `kicad-cli` behaviour

### UD-23 — The documented default report filename is wrong on **both** the name and the location **[DOC]**

**Docs say** ([kicad-cli reference](https://docs.kicad.org/master/en/cli/cli.html), for
both `pcb drc` and `sch erc`): *"When `--output` is not used, the output filename will be
the same as the input file, with the `.rpt` or `.json` file extension, depending on the
selected format."*

**Evidence** — run from a directory that is **not** the input's:

```
$ cd /work/run && kicad-cli pcb drc --format json ../in/board.kicad_pcb
Found 4 violations
Found 3 unconnected items
Saved DRC Report to board-drc.json
exit=0
$ ls ../in    ->  board.kicad_pcb  board.kicad_prl     # no report here
$ ls .        ->  board-drc.json                        # here

$ kicad-cli sch erc --format json /work/t1c/sheet.kicad_sch
Saved ERC Report to sheet-erc.json                       # again, in the CWD
```

**Correct statement.** *The report is written to the **current working directory**, not
beside the input, as `<input-basename>-drc.<ext>` / `<input-basename>-erc.<ext>`.* Two
documented facts wrong in one sentence, times two subcommands.

**Why it matters.** A CI script that looks for the report beside the input finds nothing
and cannot tell that from "no violations".

---

### UD-24 — `--save-board` silently no-ops: the **help text** implies a constraint that is not enforced **[BUG]** (narrow)

**Claim.** `--save-board` without `--refill-zones` exits 0, prints nothing, and does not
save. The **online docs describe this correctly**; `--help` does not.

**Evidence** on `pic_programmer.kicad_pcb` (4 zones), md5 before → after:

| args | exit | md5 | result |
|---|---|---|---|
| *(none)* | 0 | `33c364f6…` → `33c364f6…` | unchanged |
| `--save-board` | 0 | `33c364f6…` → `33c364f6…` | **unchanged, no diagnostic** |
| `--refill-zones` | 0 | `33c364f6…` → `33c364f6…` | unchanged |
| `--save-board --refill-zones` | 0 | `33c364f6…` → `9871f925…` | **rewritten** (+ stdout line `Saved board`) |

**Docs vs help.** docs.kicad.org: *"Save the board after running DRC. The board will not be
saved unless `--refill-zones` is also used."* — accurate. `--help`: *"Save the board after
DRC, must be used with --refill-zones"* — "must be used with" reads as a precondition that
will be enforced. It is not: the flag is accepted and ignored, with exit 0 and silence.

**Why it matters.** A user asking for a save gets a success exit and no save. The fix is
either to enforce the precondition (exit 1) or to reword `--help` to match the online docs.

---

### UD-25 — A malformed `-D` exits 1 with **no diagnostic at all** **[BUG]**

**Evidence.**

```
$ kicad-cli pcb drc -D JUSTAKEY -o /tmp/o/out.rpt board.kicad_pcb
exit=1
stdout: 0 bytes
stderr: 0 bytes
files in /tmp/o: []
```

Same for `sch erc -D JUSTAKEY`, and with no `-o` at all. **Also `-D KEY=`** (an `=` with an
empty value) — so the rule is "no non-empty value", not merely "no `=`".

Controls that *do* report: valid `-D MYKEY=MYVAL` → exit 0 with a report;
`--format bogus` → exit 1, stderr `Invalid report format`; `--nope` → exit 1,
`Unknown argument: --nope` plus usage.

**Related, and undocumented:** argparse-level errors (`Unknown argument`,
`input: 1 argument(s) expected. 0 provided.`) go to **stdout**; KiCad-level validation
errors (`Invalid report format`, `Invalid units specified`) go to **stderr**.

**Why it matters.** Exit 1 with zero bytes on both streams is unactionable — a script
cannot distinguish it from a crash, and a human cannot tell which argument was wrong.

---

### UD-26 — Exit codes 1, 2 and 3 are undocumented; 5 is documented **only** in the online reference **[DOC]**

**Claim, corrected.** The previously-circulated claim that "exit codes 1/2/3/5 are entirely
undocumented" is **partly wrong**: 5 *is* documented online. 1, 2 and 3 are documented
nowhere, and **no** exit code appears in `--help` or in a man page.

**Docs say.** docs.kicad.org, for `pcb drc` and `sch erc`: `--exit-code-violations`
*"…exit code is 0 if no violations are found, and 5 if any violations are found."* Nothing
anywhere about 1, 2 or 3.

**Evidence — the full map, all reproduced.**

| exit | class | example |
|---:|---|---|
| **1** | argument / option validation | `--format bogus` → stderr `Invalid report format`; `--nope` → stdout `Unknown argument: --nope`; missing positional; malformed `-D` (UD-25) |
| **2** | the file exists but is unusable **as a library** | `fp export svg` on a broken `.pretty` → `Unable to load library`; `sym upgrade` on a renamed `.kicad_pcb` → `Unable to load library`; `sym upgrade` on a read-only lib → `Unable to save library` |
| **3** | input missing, or exists and will not load | `Failed to load board: Unable to open … for reading.`; `Failed to load board: Unknown file type`; `Failed to load schematic`; `Symbol file does not exist or is not accessible` |
| **5** | `--exit-code-violations` **and** violations were found | `pcb drc --exit-code-violations` → `Found 4 violations` … exit 5; `sch erc --exit-code-violations` → `Found 6 violations`, exit 5 |

Two precisions found while pinning 5: **unconnected items count as violations** for the
exit code (`--severity-error` with `Found 0 violations / Found 3 unconnected items` still
gives 5), and 5 is only reachable on the success path — a load failure (3) or an argument
error (1) still wins. The 2-vs-3 split is *existence*: "does not exist or is not
accessible" → 3; exists but will not load/save → 2.

**Evidence — undocumented in the binary.** `kicad-cli --help` has no exit-code section;
grepping `-i "exit|status code|return code"` across the help of `kicad-cli`, `pcb`, `sch`,
`sym`, `fp`, `pcb export`, `sch export`, `pcb drc` and `sch erc` yields only
`-h, --help  Shows help message and exits` and the two `--exit-code-violations` lines,
which say "a nonzero exit code" without giving the value. **No man page exists** — `man`
is not installed in the image and `find / -iname "*kicad-cli*"` returns only the binary and
two shell-completion files.

**Why it matters.** Exit codes are the CLI's primary machine interface. Any script must
currently discover 1/2/3 by experiment.

---

### UD-27 — `sym export svg` accepts a board or a schematic, exits 0, writes nothing, says nothing **[BUG]**

**Evidence.**

```
$ kicad-cli sym export svg -o /tmp/o board.kicad_pcb
exit=0   stdout: 0 bytes   stderr: 0 bytes   /tmp/o: []
$ kicad-cli sym export svg -o /tmp/o sheet.kicad_sch
exit=0   stdout: 0 bytes   stderr: 0 bytes   /tmp/o: []
```

Positive control (`pic_programmer.kicad_sym`): exit 0, 30 SVGs, `Plotting symbol '24C16'
unit 1 to …` on stdout. Negative control (missing file): exit 3,
`Symbol file does not exist or is not accessible`.

**The mechanism**, which makes it worse: a board *renamed* to `.kicad_sym` gives exit 2 and
`Unable to load library`. The silence happens specifically because the **extension is not
`.kicad_sym`** — the file is skipped by extension before any parse is attempted.

**Why it matters.** Success with no output and no message is the worst possible failure
mode for automation. A CI job that exports symbol SVGs from the wrong path passes forever.

---

### UD-28 — `kicad-cli`'s `Expecting` diagnostics **double** the quotes around the token — and only board loading produces them at all **[BUG]** (cosmetic) + **[DOC]**

**Evidence.** Feeding `(kicad_pcb 42)` to `pcb export stats`, `od -c` of stderr:

```
F a i l e d   t o   l o a d   b o a r d :   E x p e c t i n g
'   '   (   '   '       i   n       '   /   t   m   p   /   b   2   .   k   i   c   a   d   _   p   c   b   '  ,
```

`cat -A`: `Failed to load board: Expecting ''('' in '/tmp/b2.kicad_pcb', line 1, offset 12.$`

Two U+0027 apostrophes either side of the **token**, one either side of the **filename**,
in the same message. Consistent across `Expecting ''('' `, `Expecting ''symbol''`,
`Expecting '')''`. **Not** present in other parse-error classes: `Unknown token 'bogus'` and
`need a number for 'version'` use normal single quotes.

**The schematic half does not exist.** Every one of `sch erc`, `sch export netlist`, `bom`,
`pdf`, `svg`, `python-bom` fed `(kicad_sch 42)` returns exit 3 and stderr that is
byte-for-byte `Failed to load schematic\n` — no `Expecting`, no line, no offset, no
filename. Library failures are likewise opaque (`Unable to load library`).

**Why it matters.** [BUG] Anything matching KiCad's parse messages must match the doubled
form (this repo's `error_contains` fixtures do). [DOC] The schematic loader's total absence
of parse detail is a real usability gap and is the reason schematic rejection cases here
can only assert a four-word string.

---

### UD-29 — Every `pcb` subcommand writes a `.kicad_prl` **next to the input** **[BUG]**

**Claim.** Not just `pcb drc`: every `pcb` subcommand tested writes
`<basename>.kicad_prl` into the **input file's** directory, including on runs that exit
nonzero. No `sch` subcommand does. If the directory is not writable the write is skipped
**silently**.

**Evidence** (fresh input dir per run, diffing the listing):

| subcommand | exit | input-dir delta |
|---|---|---|
| `pcb drc` | 0 | `+ board.kicad_prl` |
| `pcb export gerbers` | 0 | `+ board.kicad_prl` |
| `pcb export svg` | **1** | `+ board.kicad_prl` |
| `pcb export step` | 0 | `+ board.kicad_prl` |
| `sch erc` | 0 | *(none)* |
| `sch export netlist` | 0 | *(none)* |

Read-only case (dir `555`, file `444`): exit 0, report still produced, `.kicad_prl`
**not** written, no warning on either stream. Restoring the dir to `755` (file still `444`)
makes it reappear — so it is the **directory** permission that gates it.

**Why it matters.** A read-only-looking operation mutates the user's source tree. It
affects **this repo right now**: `git status` shows untracked
`suites/**/board.kicad_prl` files, and `.gitignore` does not cover `*.kicad_prl`. Anyone
running `kicad-cli pcb *` over a checked-out project gets untracked files.

---

### UD-30 — `pcb export svg` and `pcb export dxf` print a KiCad-9 deprecation banner on **stdout**, with ANSI escapes, when no `--mode-*` is given **[BUG]**

**Evidence.** `cat -A` of stdout:

```
^[[33;1mThis command has deprecated behavior as of KiCad 9.0, the default behavior of this command will change in a future release.^[[0m$
^[[33;1mThe new behavior will match --mode-multi^[[0m$
Plotted to '/tmp/o/out.svg'.$
Done.$
```

stderr is 0 bytes. Not a TTY (docker without `-t`). `NO_COLOR=1` and `TERM=dumb` do **not**
suppress the escapes.

| command | banner |
|---|---|
| `pcb export svg` (no mode flag) | **yes** |
| `pcb export svg --mode-single` / `--mode-multi` | no |
| `pcb export dxf` (no mode flag) | **yes** |
| `pcb export dxf --mode-multi` | no |
| `pcb export pdf`, `pcb export gerbers` | no |

**Why it matters.** A warning on stdout pollutes any pipeline that captures it, and the
unconditional ANSI escapes corrupt log files. This repo's `render` answer is produced by
`pcb export svg`. See §7 — the banner is **not** printed on every invocation, and it is not
unique to `svg`.

---

## 5. DRC and ERC

### UD-31 — `pcb export ipcd356` reads an **uninitialised `int`** on every via record **[BUG]**

Full entry: [`DIVERGENCES.md` DIV-0002](DIVERGENCES.md). Summary:
`pcbnew/exporters/export_d356.cpp` initialises `D356_RECORD::soldermask` on the **pad**
path (`rk.soldermask = 3;`, line 151) and never on the **via** path, which goes straight to
`rk.soldermask |= 1` / `|= 2` (lines 231/233) on an uninitialised struct member and prints
it with `fprintf(aFile, "S%d\n", …)` (line 359).

The release build reads `3` on every board tried, including three with **no pads at all** —
so it is not leakage from the pad loop, just a stack slot that happens to hold 3:

```
micro-via              317NET-1  VIA  MD0157PA00X+001969Y-003937X0236Y0000R000S3
blind-and-buried-vias  307NET-1  VIA  MD0118PA01X+003150Y-003937X0236Y0000R000S3
```

The gcov-instrumented build (`-ftrivial-auto-var-init=pattern`) reads `S-16843009` =
`0xFEFEFEFF`.

**Why it matters here specifically:** **the `S3` values our via boards produce are luck,
not specification.** No committed answer records them (`runner/reduce.py`'s
`reduce_ipcd356` drops the trailing `S<n>`), but the reducer's regex requires a
*non-negative* serial, so seven board fixtures' `summary.json` generation depends on that
uninitialised read staying non-negative — which it does not on a pattern-initialised build.
Read DIV-0002 before writing any case that asserts IPC-D-356 soldermask codes.

---

### UD-32 — `.kicad_dru` custom rules and `.kicad_pro` severities are honoured by `kicad-cli pcb drc` and documented nowhere **[DOC]**

**Claim.** `kicad-cli pcb drc` picks up a sibling `.kicad_dru` and a sibling `.kicad_pro`
and both change the findings; the custom rule's **name** is carried into the finding text.
The `kicad-cli` reference mentions neither file.

**Docs say.** Re-fetched 2026-08-03: the `pcb drc` / `sch erc` sections of
[the kicad-cli reference](https://docs.kicad.org/master/en/cli/cli.html) contain **no
reference to `.kicad_dru` or `.kicad_pro`**. Neither does `kicad-cli pcb drc --help`,
which lists only `-o`, `-D`, `--format`, `--all-track-errors`, `--schematic-parity`,
`--units`, `--severity-*`, `--exit-code-violations`, `--refill-zones`, `--save-board` —
the word "project" does not appear.

**Evidence — `.kicad_dru`.** Board with two 1×1 mm SMD pads on nets NETA/NETB, 1.5 mm
apart; sibling rule file:

```
(version 1)
(rule "MY-WIDE-CLEARANCE-RULE"
	(constraint clearance (min 3mm))
	(condition "A.Type == 'pad' && B.Type == 'pad'"))
```

`kicad-cli pcb drc --format json --severity-all -o out.json probe.kicad_pcb`:

| directory contents | violations |
|---|---|
| `probe.kicad_pcb` | 2 (both `lib_footprint_issues`) |
| `probe.kicad_pcb` + **`probe.kicad_dru`** | **3** — adds `error \| clearance` |
| `probe.kicad_pcb` + `other.kicad_dru` | 2 (unchanged) |
| `probe.kicad_pcb` + `myproj.kicad_pro` + `myproj.kicad_dru` | 2 (unchanged) |

**The filename rule: `<board-stem>.kicad_dru`, in the board's directory** — not the CWD, and
not a project name you can point elsewhere:

```
cwd=/tmp with /tmp/probe.kicad_dru, board elsewhere without a sibling  -> clearance found: False
cwd=/tmp,                            board with a sibling probe.kicad_dru -> clearance found: True
```

There is no CLI flag to name a project; kicad-cli derives it from the **board filename**
(corroborated by the `.kicad_prl` of UD-29 always being written as `<board-stem>.kicad_prl`).

**Evidence — the rule name is carried into the finding**, verbatim from `out.json`:

```json
"description": "Clearance violation (rule 'MY-WIDE-CLEARANCE-RULE' clearance 3.0000 mm; actual 1.5000 mm)",
"severity": "error",
"type": "clearance"
```

and `--format report` adds a dedicated line: `Rule: MY-WIDE-CLEARANCE-RULE; error`.

**Evidence — `.kicad_pro` severities**, same board with a sibling `probe.kicad_pro`
setting `board.design_settings.rule_severities.lib_footprint_issues`:

| `.kicad_pro` | result |
|---|---|
| none | `Found 2 violations`; `ignored_checks` = KiCad's 5 defaults |
| `probe.kicad_pro`, `= "ignore"` | **`Found 0 violations`**; the key moves into `ignored_checks` |
| `myproj.kicad_pro`, `= "ignore"` | `Found 2 violations` — not loaded (**stem must match**) |
| `probe.kicad_pro`, `= "error"` | 2 violations, severity raised `warning` → `error` |

and the raise changes the **process exit code**:

```
$ kicad-cli pcb drc --exit-code-violations --severity-error -o /dev/null probe.kicad_pcb
exit=5      # with probe.kicad_pro raising lib_footprint_issues to error
exit=0      # identical board, no .kicad_pro
```

**Why it matters.** This is the most consequential omission in the CLI page: a user cannot
tell from the docs that `kicad-cli pcb drc` runs **their project's** rules rather than
defaults — so a CI job's DRC result, and its exit code, silently depend on two sibling
files nobody told them were read, keyed off the board's filename.

---

### UD-33 — `sch erc --format json` scales **every schematic dimension** down by exactly 100; `--format report` is correct **[BUG]**

**Claim, sharpened.** It is not only `pos`. Both `pos.x`/`pos.y` **and any length formatted
into an item's `description` string** come out 100× too small, in every unit system.
`--format report` is correct in every unit, and `pcb drc --format json` is correct in every
unit — so it is ERC-JSON-specific.

**Evidence.** Schematic with two Output pins joined by a wire; `U1` at exactly (100, 100)
mm, `U2` at (110, 100) mm.

`--format report --severity-all` — **correct**:

```
[pin_to_pin]: Pins of type Output and Output are connected
    ; error
    @(100.00 mm, 100.00 mm): Symbol U1 Pin 1 [O, Output, Line]
    @(110.00 mm, 100.00 mm): Symbol U2 Pin 1 [O, Output, Line]
```

and correct in the other units too: `@(3.937 in, 3.937 in)`, `@(3937 mils, 3937 mils)`.

`--format json --severity-all` — **all 100× small**:

| `--units` | `coordinate_units` | U1 pin `pos.x` | correct value | ratio |
|---|---|---|---|---:|
| `mm` | `"mm"` | `1.0` | `100.0` | **100.000** |
| `in` | `"in"` | `0.03937007874015748` | `3.937007874015748` | **100.000** |
| `mils` | `"mils"` | `39.37007874015748` | `3937.007874015748` | **100.000** |

`U2` at 110 mm gives `1.1` / `0.04330708661417323` / `43.30708661417323` — the same exact
factor, so it is a **ratio, not an offset**. A second schematic with non-round coordinates
confirms it on x, y **and** a length:

```
report:  @(20.00 mm, 50.80 mm)  @(165.10 mm, 50.80 mm)   "Horizontal Wire, length 145.10 mm"
json:    {'x': 0.2, 'y': 0.508} {'x': 1.651, 'y': 0.508} "Horizontal Wire, length 1.4510 mm"
```

**Control — the PCB side is correct.** The same clearance violation, pads at (10,10) and
(12.5,10), through `pcb drc --format json`:

```
--units mm:   [(10.0, 10.0), (12.5, 10.0)]                            "clearance 3.0000 mm; actual 1.5000 mm"
--units in:   [(0.3937007874015748, …), (0.4921259842519685, …)]      "clearance 0.1181 in;  actual 0.0591 in"
--units mils: [(393.7007874015748, …), (492.12598425196853, …)]       "clearance 118.11 mils; actual 59.06 mils"
```

**Likely mechanism.** 100 is exactly `IU_PER_MM(pcb) / IU_PER_MM(sch)` = `1e6 / 1e4` — the
ERC JSON path appears to build a units provider with the **board's** internal-unit scale
(1 IU = 1 nm) and apply it to schematic coordinates (1 IU = 100 nm). The differing
precision between the two outputs (`0.1000 mm` in JSON vs `10.00 mm` in the report)
supports a separate, wrongly-configured provider rather than a stray multiply.

**Scope.** `pos.x`, `pos.y` and description-embedded lengths are the only numeric-bearing
fields in the ERC JSON, and **both** are affected. `uuid`, `severity`, `type`,
`included_severities`, `ignored_checks` and `kicad_version` are unaffected.
`coordinate_units` is a *correct label for wrong values* — it reports the unit the user
asked for, so it **cannot be used to compensate**.

**Why it matters.** Any consumer of `erc.v1.json` positions is silently getting garbage.
For this repo: an `erc` extra that records positions must not be trusted until this is
fixed, and a case that pins it needs a `known_divergence` ([DL-0018](DECISIONS.md)) plus a
[`DIVERGENCES.md`](DIVERGENCES.md) entry. Highest-value upstream filing in this file after
UD-11.

---

### UD-34 — `via_diameter` is a violation type you cannot configure **[BUG]**

**Claim.** `via_diameter` is emitted as a violation `type` but is **not** an accepted
`board.design_settings.rule_severities` key. It cannot be ignored or re-graded.

**Evidence.** Board with a 0.6 mm via plus a `.kicad_dru` rule
`(constraint via_diameter (min 2mm))`, which fires. A sibling `.kicad_pro` then sets
**three** keys to `"ignore"` — `via_diameter`, `via_dangling`, `lib_footprint_issues`:

```json
"ignored_checks": [
    { "description": "Via is not connected or connected on only one layer", "key": "via_dangling" },
    …5 KiCad defaults…,
    { "description": "Footprint not found in libraries", "key": "lib_footprint_issues" }
],
"violations": [
    { "description": "Via diameter (rule 'VIA-MUST-BE-HUGE' min diameter 2.0000 mm; actual 0.6000 mm)",
      "severity": "error", "type": "via_diameter" }
]
```

Both controls were honoured — they appear in `ignored_checks` and their violations are
gone. `via_diameter` appears **nowhere** in `ignored_checks` and its violation survives at
`error`. It is also absent from the union of every `rule_severities` key across all shipped
demo projects (64 DRC keys).

**Why it matters.** An un-silenceable finding is an un-greenable board: a project that
legitimately wants to accept a via diameter has no way to say so. Either the key is missing
from the settings map or the item is missing from KiCad's severities panel.

---

### UD-35 — Six severity identifiers in KiCad's own shipped demos are silently discarded — but `hole_near_hole` is a **live alias**, not one of them **[BUG]** (upstream data)

**Claim, corrected.** Six of the seven previously-listed identifiers are dead;
`hole_near_hole` is **not** — it is accepted as a legacy alias for `hole_to_hole`.

**Evidence — presence in shipped demos** (`grep -rl` over `/usr/share/kicad/demos/**/*.kicad_pro`):

| key | section | demo files |
|---|---|---:|
| `hole_near_hole` | DRC | 18 |
| `overlapping_pads` | DRC | 6 |
| `zone_has_empty_net` | DRC | 3 |
| `bus_label_syntax` | ERC | 16 |
| `conflicting_netclasses` | ERC | 30 |
| `global_label_dangling` | ERC | 36 |
| `overlapping_rule_areas` | ERC | 4 |

**Evidence — the echo test.** A `.kicad_pro` setting all 64 DRC and all 50 ERC keys found
across the demos to `"ignore"`; a recognised key is echoed back in the run's
`ignored_checks`:

```
DRC: requested 64, echoed 62 -> not echoed: hole_near_hole, overlapping_pads, zone_has_empty_net
ERC: requested 50, echoed 46 -> not echoed: bus_label_syntax, conflicting_netclasses,
                                            global_label_dangling, overlapping_rule_areas
```

**Evidence — isolation against a fabricated key.** Each key alone, diffed against the
default ignored set: `overlapping_pads`, `zone_has_empty_net`, `bus_label_syntax`,
`conflicting_netclasses`, `global_label_dangling`, `overlapping_rule_areas` and the invented
`BOGUS_KEY_XYZ` all add **nothing** — indistinguishable. Controls (`hole_to_hole`,
`shorting_items`, `label_dangling`) each add themselves.

**Evidence — behaviour, not just the echo.**

`global_label_dangling` is dead. One schematic, one global label (`single_global_label` is
ignored by default):

```
no .kicad_pro                    -> single_global_label reported: False
global_label_dangling = "error"  -> single_global_label reported: False   (no effect)
single_global_label   = "error"  -> single_global_label reported: True
```

`hole_near_hole` is **alive**. Two 0.3 mm PTH holes 0.4 mm apart:

```
no .kicad_pro             -> [('warning', 'hole_to_hole')]
hole_near_hole = "error"  -> [('error',   'hole_to_hole')]     <-- it works
hole_to_hole   = "error"  -> [('error',   'hole_to_hole')]
hole_near_hole = "ignore" -> suppressed, hole_to_hole listed in ignored_checks
# conflicting values: the canonical key wins
near=ignore, to=error     -> [('error', 'hole_to_hole')]
to=ignore,   near=error   -> IGNORED
```

The demos ship **both** `hole_near_hole` and `hole_to_hole` in the same file, which is the
tell that one is a migration alias.

**Limits.** For `overlapping_pads`, `zone_has_empty_net`, `bus_label_syntax`,
`conflicting_netclasses` and `overlapping_rule_areas` the evidence is the
echo/bogus-key equivalence only — a settings-loader observation, not a check-firing one.
Their modern replacements were not triggered. Treat those five as *strongly indicated*, not
behaviourally proven.

**Why it matters.** "The identifiers in a project file" is **not** a safe source for a
severity-key catalogue — including KiCad's own demos. Anyone regenerating one must probe
the binary with the echo method. And a project silently carrying a dead key gets defaults
where it thinks it configured something.

---

## 6. Building KiCad

### UD-36 — KiCad 10 requires build dependencies Debian's KiCad-9 metadata does not list **[UNDOC-OK]**

**Claim.** `apt-get build-dep kicad` on Debian trixie (which packages KiCad 9.0.2) is
**not sufficient** to configure KiCad 10. Three packages are needed on top, and one of them
exists only for a GUI feature that `kicad-cli` cannot use.

**Evidence** — read from the pinned KiCad 10 source tree
(`18fb9289ff0efdca53c0352ed81a0973f0a6b58c`):

```
CMakeLists.txt:834      if( UNIX AND NOT APPLE )
CMakeLists.txt:835          find_package( SPNAV REQUIRED )
CMakeLists.txt:845      find_package( Pixman 0.30 REQUIRED )
CMakeLists.txt:1096     find_package( wxWidgets … COMPONENTS gl aui adv html core net base
                                      propgrid xml stc richtext webview REQUIRED )
libs/kiplatform/CMakeLists.txt:93   find_package( Poppler REQUIRED )
```

| package | why | note |
|---|---|---|
| `libspnav-dev` | `find_package( SPNAV REQUIRED )` is **unconditional** on non-Apple UNIX | there is no option to switch it off, even though a 3Dconnexion space mouse is pure GUI and irrelevant to `kicad-cli` |
| `libwxgtk-webview3.2-dev` | the wxWidgets `webview` component is REQUIRED | Debian splits it out; `libwxgtk3.2-dev` does **not** depend on it |
| `libpoppler-cpp-dev`, `libpoppler-glib-dev`, `libpoppler-private-dev` | `find_package( Poppler REQUIRED )` on GTK builds (PDF printing) | `cmake/FindPoppler.cmake`'s Core component wants poppler's own headers, which Debian keeps in `libpoppler-private-dev` |
| `libpixman-1-dev` | `find_package( Pixman 0.30 REQUIRED )` | transitively present via `libcairo2-dev`; listed explicitly so it cannot silently disappear |

**Why it matters.** Anyone building KiCad 10 from source on a Debian-9-era dependency list
gets three separate CMake configure failures, each of which reads as a missing optional
feature rather than a hard requirement. This project hit all three
(`tools/coverage/Dockerfile`, whose comments carry the same line references, plus
`tools/coverage/verify-builddeps.sh` which re-derives the upstream list so drift is
detectable).

---

## 7. Claims that did **not** survive re-verification

Kept, not deleted: a retracted claim that leaves no trace gets rediscovered and
re-believed. Each of these circulated in agent reports or research files and is **wrong or
materially imprecise**.

| # | The claim as circulated | What is actually true |
|---|---|---|
| R1 | "Board net ordinals are gone: the documented `(net NET_NUMBER)` is rejected with `Expecting net name`." | **Only pads reject it.** Segments and vias still resolve ordinals through a top-level nets section (which is also still accepted); zones accept and silently discard the assignment. See **UD-01**. The generalisation almost certainly came from this repo's own `rejects-numeric-only-net` fixture, which is a *pad* case. |
| R2 | "Zones no longer carry `(net_name …)`." | KiCad no longer *writes* it, but a zone still **accepts** `(net_name "GND")` and rewrites it as `(net "GND")`. `net_name` **is** rejected on pads, segments and vias. See **UD-01**. |
| R3 | "A future `(version 99999999)` is rejected in a board but **silently accepted in a schematic** — an inconsistency." | **False.** The schematic rejects it (exit 3, `Failed to load schematic`), as does a symbol library (exit 2, `Unable to load library`). Schematics reject anything past `20260306`, symbol libs past `20251024`. The real asymmetry is **diagnostic quality** and **exit code**, not acceptance. See **UD-13**. |
| R4 | "Without a `.kicad_pro`, repeated sheet instances get no reference disambiguation." | The collapse is real but the `.kicad_pro` is **not the lever** — adding one (with or without a matching `(project "NAME")`) produces byte-identical output. The reference comes from the **first `(path …)` entry in the file**, established by swapping the two blocks. See **UD-16**. |
| R5 | "Hierarchical-label nets are sheet-scoped (`/sub/SIG`); global labels are bare." | True as far as it goes, but **plain local labels are prefixed identically** (`/sub/LSIG`). Path-prefixing does not distinguish hierarchical from local. See **UD-15**. |
| R6 | "`Description` is a de-facto mandatory fifth property." | Right about the writer, wrong about the reader: KiCad writes **all five** properties unconditionally, synthesizing any that are absent with an empty value, and **requires none** of them on read. See **UD-21**. |
| R7 | "Exit codes 1/2/3/5 are entirely undocumented." | **5 is documented** on docs.kicad.org under `--exit-code-violations`. 1, 2 and 3 are undocumented, and no exit code appears in `--help` or a man page (there is none). See **UD-26**. |
| R8 | "`--save-board`'s documented constraint is unenforced and silently no-ops." | The **online docs are correct** ("The board will not be saved unless `--refill-zones` is also used"). Only `--help`'s "must be used with" wording implies an enforcement that does not exist. See **UD-24**. |
| R9 | "`pcb export svg` prints a KiCad 9 deprecation banner on **every** invocation." | Only when neither `--mode-single` nor `--mode-multi` is given — and `pcb export dxf` does it too. See **UD-30**. |
| R10 | "KiCad's demos contain seven dead rule identifiers, including `hole_near_hole`." | **Six** are dead. `hole_near_hole` is a **live legacy alias** for `hole_to_hole` — setting it re-grades and suppresses real `hole_to_hole` findings, and the canonical key wins when both are present. Listing it as rejected would be wrong. See **UD-35**. |

Two further sharpenings, not retractions:

- **`.kicad_prl` is written by every `pcb` subcommand**, not just `pcb drc`, and by **no**
  `sch` subcommand (UD-29).
- **`suppress_zeros` is not merely a spelling difference** — the documented spelling makes
  the load *fail*, with a message that identifies neither the token nor the file (UD-03).

---

## 8. Upstream filing queue

Ordered by how much damage the bug can do silently. None filed yet.

| Priority | Entry | Why first |
|---|---|---|
| 1 | **UD-11** — drill offset is backwards in the docs | produces physically wrong boards; no check catches it |
| 2 | **UD-33** — ERC JSON scales every dimension by 1/100 | every consumer silently gets garbage; a one-line scale fix |
| 3 | **UD-31** — uninitialised read in the IPC-D-356 via path ([DIV-0002](DIVERGENCES.md)) | undefined behaviour in a fab-output path |
| 4 | **UD-27** — `sym export svg` succeeds silently on the wrong file type | success with no output is the worst automation failure mode |
| 5 | **UD-16** — repeated sheet instances collapse references | wrong BOM/netlist with only a generic warning |
| 6 | **UD-17** — `bus_alias` dropped and ignored | round-trip data loss through a documented token |
| 7 | **UD-09** — malformed UUIDs silently regenerated | silent identity replacement breaks external references |
| 8 | **UD-34** — `via_diameter` cannot be silenced | an un-greenable board with no workaround |
| 9 | **UD-01**, **UD-03**, **UD-05**, **UD-19**, **UD-20**, **UD-21**, **UD-23**, **UD-32** | documentation PRs — mechanical, high value, low risk. UD-32 (`.kicad_dru`/`.kicad_pro` are read) is the single most consequential omission in the CLI reference. |
| 10 | **UD-25**, **UD-28**, **UD-29**, **UD-30**, **UD-24** | diagnostic and side-effect quality |
| 11 | **UD-35** — dead severity keys in KiCad's shipped demo projects | upstream *data* fix; also warrants a migration warning when an unknown key is loaded |

---

## 9. Maintaining this file

- **Every entry must be re-runnable.** An entry without a command and its observed output
  is a rumour. Mark anything you did not personally re-run **(unverified)**, as several
  entries above are.
- **Re-verify the whole file on a KiCad version bump.** These are behaviours of 10.0.5;
  a bump is exactly when a `[BUG]` becomes a resolved entry and a `[DOC]` gap may close.
- **A retracted claim moves to §7, it does not get deleted.**
- **An entry that a test case now pins should say so**, and link the case — that is the
  point of the suite. A `[BUG]` that a case pins as a strict xfail belongs additionally in
  [`DIVERGENCES.md`](DIVERGENCES.md) ([DL-0018](DECISIONS.md)).
