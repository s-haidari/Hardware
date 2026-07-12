"""BOM diff: compare two bills of materials (rev A -> rev B) and report what changed —
lines added, removed, or with a moved quantity. Lines match by MPN, else by
value+footprint (the same identity key the consolidated BOM groups on), so a rename of
the value on an MPN'd part doesn't read as add+remove.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402


def test_diff_reports_added_removed_and_qty_changes():
    a = [
        {"mpn": "TPS2121RUXR", "value": "TPS2121", "qty": 1},
        {"mpn": "STM32F407VGT6", "value": "STM32", "qty": 1},
        {"mpn": "", "value": "10k", "footprint": "R_0402", "qty": 4},   # passive, by value
    ]
    b = [
        {"mpn": "TPS2121RUXR", "value": "TPS2121", "qty": 2},           # qty 1 -> 2
        {"mpn": "", "value": "10k", "footprint": "R_0402", "qty": 4},   # unchanged
        {"mpn": "GRM188", "value": "100n", "qty": 3},                   # new
    ]
    d = L.bom_diff(a, b)
    assert {r["mpn"] for r in d["added"]} == {"GRM188"}
    assert {r["mpn"] for r in d["removed"]} == {"STM32F407VGT6"}
    chg = {r["mpn"]: r for r in d["changed"]}
    assert chg["TPS2121RUXR"]["from_qty"] == 1 and chg["TPS2121RUXR"]["to_qty"] == 2
    assert chg["TPS2121RUXR"]["delta"] == 1
    assert d["unchanged"] == 1                                          # the 10k passive


def test_diff_entries_carry_footprint_for_re_keying():
    # Each entry keeps its footprint so a consumer (e.g. the cost column) can re-key it
    # to the canonical line identity — passives that match only on value+footprint need it.
    a = [{"mpn": "", "value": "10k", "footprint": "R_0402", "qty": 2}]
    b = [{"mpn": "GRM188", "value": "100n", "footprint": "C_0402", "qty": 1}]
    d = L.bom_diff(a, b)
    assert d["added"][0]["footprint"] == "C_0402"
    assert d["removed"][0]["footprint"] == "R_0402"


def test_diff_matches_passives_by_value_and_footprint():
    a = [{"mpn": "", "value": "10k", "footprint": "R_0402", "qty": 2}]
    b = [{"mpn": "", "value": "10k", "footprint": "R_0603", "qty": 2}]  # different footprint
    d = L.bom_diff(a, b)
    # Same value but a different footprint is a different part -> add + remove, not a match.
    assert len(d["added"]) == 1 and len(d["removed"]) == 1
    assert not d["changed"]


def test_diff_is_case_insensitive_on_mpn_and_aggregates_duplicate_lines():
    a = [{"mpn": "abc-1", "qty": 2}, {"mpn": "ABC-1", "qty": 3}]        # same part, two lines
    b = [{"mpn": "Abc-1", "qty": 5}]
    d = L.bom_diff(a, b)
    assert not d["added"] and not d["removed"] and not d["changed"]     # 5 == 2+3
    assert d["unchanged"] == 1


def test_diff_consolidated_rows_use_total_qty():
    a = [{"mpn": "X", "total_qty": 3}]
    b = [{"mpn": "X", "total_qty": 10}]
    d = L.bom_diff(a, b)
    assert d["changed"][0]["from_qty"] == 3 and d["changed"][0]["to_qty"] == 10


def test_diff_csv_lists_every_change():
    a = [{"mpn": "KEEP", "qty": 1}, {"mpn": "GONE", "qty": 1}]
    b = [{"mpn": "KEEP", "qty": 1}, {"mpn": "NEW", "qty": 2}]
    d = L.bom_diff(a, b)
    head, *lines = d["csv"].strip().splitlines()
    assert head == "Change,MPN,Value,From Qty,To Qty,Delta"
    body = "\n".join(lines)
    assert "Added,NEW,,0,2,2" in body
    assert "Removed,GONE,,1,0,-1" in body
    assert "KEEP" not in body                                           # unchanged omitted from the diff


# ── bom_rows_from_csv: read an exported BOM back into diff-ready rows ──────────
def test_rows_from_csv_parses_project_export():
    # The project BOM export header: Refs,Qty,Value,MPN,Manufacturer,Footprint,...
    text = ("Refs,Qty,Value,MPN,Manufacturer,Footprint,Datasheet,Description,Basic\n"
            "R1,R2,4,10k,,,MyFootprints:R_0402,,,yes\n")
    # (that malformed row has refs split across cells) — use a clean, quoted refs cell:
    text = ('Refs,Qty,Value,MPN,Manufacturer,Footprint,Datasheet,Description,Basic\n'
            '"R1,R2",4,10k,,,MyFootprints:R_0402,,,yes\n'
            '"U1",1,TPS2121,TPS2121RUXR,TI,SOIC,,,\n')
    rows = L.bom_rows_from_csv(text)
    by_key = {(r["mpn"] or r["value"]): r for r in rows}
    assert by_key["10k"]["qty"] == 4 and by_key["10k"]["footprint"] == "MyFootprints:R_0402"
    assert by_key["TPS2121RUXR"]["qty"] == 1


def test_rows_from_csv_parses_consolidated_export_total_column():
    # The consolidated export uses "Total" for quantity, MPN/Value/Footprint columns.
    text = ("MPN,Manufacturer,Value,Footprint,Total,BoardA,Datasheet\n"
            "GRM188,Murata,100n,C_0402,7,7,\n")
    rows = L.bom_rows_from_csv(text)
    assert rows == [{"mpn": "GRM188", "value": "100n", "footprint": "C_0402", "qty": 7}]


def test_rows_from_csv_round_trips_through_bom_diff():
    # An exported BOM re-parsed then diffed against the current rows shows the change.
    old_csv = ("Refs,Qty,Value,MPN,Manufacturer,Footprint,Datasheet,Description,Basic\n"
               "U1,1,TPS2121,TPS2121RUXR,TI,SOIC,,,\n")
    old_rows = L.bom_rows_from_csv(old_csv)
    new_rows = [{"mpn": "TPS2121RUXR", "value": "TPS2121", "footprint": "SOIC", "qty": 3}]
    d = L.bom_diff(old_rows, new_rows)
    assert d["changed"][0]["from_qty"] == 1 and d["changed"][0]["to_qty"] == 3


def test_rows_from_csv_bad_input_is_empty():
    assert L.bom_rows_from_csv("") == []
    assert L.bom_rows_from_csv("not,a,bom,header\n1,2,3,4\n") == []     # no MPN/Value column
    assert L.bom_rows_from_csv(None) == []


# ── bom_diff_cost: what the change COSTS, from the newer revision's prices ─────
def test_diff_cost_prices_added_and_changed_from_rev_b():
    # rev A (old, unpriced) -> rev B (current build, priced).
    a = [{"mpn": "KEEP", "qty": 1}, {"mpn": "GROW", "qty": 2}]
    b = [{"mpn": "KEEP", "qty": 1, "unit_price": 5.0},                  # unchanged -> $0
         {"mpn": "GROW", "qty": 5, "unit_price": 2.0},                  # +3 units @ $2 = +$6
         {"mpn": "NEW", "qty": 4, "unit_price": 0.25}]                  # added: 4 @ $0.25 = +$1
    c = L.bom_diff_cost(a, b)
    assert c["priced"] is True
    assert c["added_cost"] == 1.00
    assert c["changed_cost"] == 6.00
    assert c["delta"] == 7.00                                           # net +$7.00/board
    assert c["removed_unpriced"] == 0


def test_diff_cost_shrink_is_a_saving():
    a = [{"mpn": "X", "qty": 10}]
    b = [{"mpn": "X", "qty": 4, "unit_price": 1.50}]                    # -6 units @ $1.50
    c = L.bom_diff_cost(a, b)
    assert c["changed_cost"] == -9.00 and c["delta"] == -9.00          # a net saving


def test_diff_cost_flags_removed_lines_it_cannot_price():
    # A removed line exists only in rev A, which was never priced -> its cost is unknown.
    a = [{"mpn": "DROP", "qty": 3}, {"mpn": "KEEP", "qty": 1}]
    b = [{"mpn": "KEEP", "qty": 1, "unit_price": 4.0}]
    c = L.bom_diff_cost(a, b)
    assert c["removed_unpriced"] == 1
    assert c["delta"] == 0.0                                            # only KEEP, unchanged
    assert c["priced"] is True                                         # rev B did carry prices


def test_diff_cost_unpriced_rev_b_reports_not_priced():
    a = [{"mpn": "X", "qty": 1}]
    b = [{"mpn": "X", "qty": 2}, {"mpn": "Y", "qty": 1}]               # no unit_price anywhere
    c = L.bom_diff_cost(a, b)
    assert c["priced"] is False
    assert c["delta"] == 0.0 and c["added_cost"] == 0.0


def test_diff_csv_adds_cost_column_from_rev_b_prices():
    a = [{"mpn": "GROW", "qty": 2}, {"mpn": "DROP", "qty": 1}]
    b = [{"mpn": "GROW", "qty": 5, "unit_price": 2.0},                  # +3 @ $2 = 6.00
         {"mpn": "NEW", "qty": 4, "unit_price": 0.25}]                  # 4 @ $0.25 = 1.00
    d = L.bom_diff(a, b)
    csv = L.bom_diff_csv(d, b)
    head, *lines = csv.strip().splitlines()
    assert head == "Change,MPN,Value,From Qty,To Qty,Delta,Cost Delta"
    body = "\n".join(lines)
    assert "Added,NEW,,0,4,4,1.00" in body
    assert "Changed,GROW,,2,5,3,6.00" in body
    assert "Removed,DROP,,1,0,-1," in body                             # removed -> blank cost cell


def test_diff_csv_omits_cost_column_when_rev_b_unpriced():
    # No usable price in rev B -> the CSV matches bom_diff's plain, price-free form.
    a = [{"mpn": "X", "qty": 1}]
    b = [{"mpn": "X", "qty": 2}]
    d = L.bom_diff(a, b)
    csv = L.bom_diff_csv(d, b)
    assert csv.strip().splitlines()[0] == "Change,MPN,Value,From Qty,To Qty,Delta"
    assert "Changed,X,,1,2,1" in csv


def test_diff_cost_uses_string_prices_and_totals_only_priced_lines():
    # Mouser prices are strings; an added line with no price must not sink the delta.
    a = []
    b = [{"mpn": "P", "qty": 2, "unit_price": "$1.25"},                 # +2 @ 1.25 = +$2.50
         {"mpn": "Q", "qty": 3, "unit_price": "Call"}]                  # unpriceable -> skipped
    c = L.bom_diff_cost(a, b)
    assert c["added_cost"] == 2.50 and c["delta"] == 2.50
    assert c["priced"] is True


def test_diff_lead_added_part_becomes_the_critical_path():
    # A long-lead part this revision ADDS gates the whole current build.
    a = [{"mpn": "KEEP", "qty": 1}]                                     # rev A: unpriced, no lead
    b = [{"mpn": "KEEP", "qty": 1, "lead_time": "2 Weeks"},
         {"mpn": "NEW", "qty": 2, "lead_time": "16 Weeks"}]             # added, longest lead
    ld = L.bom_diff_lead(a, b)
    assert ld["added_max_weeks"] == 16 and ld["added_critical_mpn"] == "NEW"
    assert ld["build_max_weeks"] == 16 and ld["build_critical_mpn"] == "NEW"
    assert ld["on_critical_path"] is True
    assert ld["any"] is True


def test_diff_lead_added_below_build_critical_is_off_the_path():
    # The added part's lead is shorter than a pre-existing part's -> not on the critical path.
    a = [{"mpn": "KEEP", "qty": 1}]
    b = [{"mpn": "KEEP", "qty": 1, "lead_time": "20 Weeks"},            # pre-existing, gates order
         {"mpn": "NEW", "qty": 2, "lead_time": "4 Weeks"}]              # added, short lead
    ld = L.bom_diff_lead(a, b)
    assert ld["added_max_weeks"] == 4 and ld["added_critical_mpn"] == "NEW"
    assert ld["build_max_weeks"] == 20 and ld["build_critical_mpn"] == "KEEP"
    assert ld["on_critical_path"] is False


def test_diff_lead_ignores_qty_only_changes():
    # A qty change keeps a part already present -> it introduces NO new lead exposure.
    a = [{"mpn": "GROW", "qty": 2}]
    b = [{"mpn": "GROW", "qty": 5, "lead_time": "16 Weeks"}]            # qty moved, part pre-existed
    ld = L.bom_diff_lead(a, b)
    assert ld["added_max_weeks"] is None                               # nothing was ADDED
    assert ld["on_critical_path"] is False
    assert ld["build_max_weeks"] == 16                                 # the build still has the lead
    assert ld["removed_unassessed"] == 0


def test_diff_lead_flags_removed_lines_it_cannot_assess():
    # A removed line exists only in unpriced rev A -> its lead can't be read, only flagged.
    a = [{"mpn": "DROP", "qty": 1}, {"mpn": "KEEP", "qty": 1}]
    b = [{"mpn": "KEEP", "qty": 1, "lead_time": "8 Weeks"}]
    ld = L.bom_diff_lead(a, b)
    assert ld["removed_unassessed"] == 1
    assert ld["added_max_weeks"] is None                               # KEEP is unchanged, not added
    assert ld["any"] is True


def test_diff_lead_no_lead_data_reports_none():
    a = []
    b = [{"mpn": "X", "qty": 1}, {"mpn": "Y", "qty": 2}]               # no lead_time anywhere
    ld = L.bom_diff_lead(a, b)
    assert ld["added_max_weeks"] is None and ld["build_max_weeks"] is None
    assert ld["any"] is False and ld["on_critical_path"] is False


def test_diff_csv_adds_lead_column_for_added_lines():
    # An added long-lead part shows its lead in the shared diff CSV; changed/removed blank.
    a = [{"mpn": "GROW", "qty": 2}, {"mpn": "DROP", "qty": 1}]
    b = [{"mpn": "GROW", "qty": 5, "unit_price": 2.0, "lead_time": "2 Weeks"},   # changed
         {"mpn": "NEW", "qty": 1, "unit_price": 5.0, "lead_time": "16 Weeks"}]   # added, 16 wk
    d = L.bom_diff(a, b)
    csv = L.bom_diff_csv(d, b)
    head, *lines = csv.strip().splitlines()
    assert head == "Change,MPN,Value,From Qty,To Qty,Delta,Cost Delta,Lead (wks)"
    body = "\n".join(lines)
    assert "Added,NEW,,0,1,1,5.00,16" in body                         # the added part's lead
    assert "Changed,GROW,,2,5,3,6.00," in body                        # changed -> blank lead cell
    assert "Removed,DROP,,1,0,-1,," in body                           # removed -> blank cost & lead


def test_diff_csv_omits_lead_column_when_no_added_line_has_lead():
    # A changed line carrying lead does NOT open the column — only ADDED parts introduce lead.
    a = [{"mpn": "GROW", "qty": 2}]
    b = [{"mpn": "GROW", "qty": 5, "unit_price": 2.0, "lead_time": "16 Weeks"}]
    d = L.bom_diff(a, b)
    csv = L.bom_diff_csv(d, b)
    assert csv.strip().splitlines()[0] == "Change,MPN,Value,From Qty,To Qty,Delta,Cost Delta"
