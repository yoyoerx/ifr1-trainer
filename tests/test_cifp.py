"""Unit tests for navdata.cifp.

Fixture lines are verbatim FAA cycle-2609 CIFP records (public domain).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from navdata.cifp import parse_cifp  # noqa: E402

# --- real records --------------------------------------------------------- #
JFK_VOR = "SUSAD        JFK   K6011590VDHW N40375838W073461701    N40375840W073461700W0120000102     NARKENNEDY                       254462609"
DPK_VOR = "SUSAD        DPK   K6011770VDLW N40473036W073181324    N40473036W073181324W0120001171     NARDEER PARK                     251632407"
HTO_VOR = "SUSAD        HTO   K6011360VTHW N40550839W072190014    N40550839W072190014W0130000222     NARHAMPTON                       253872012"
# DME/TACAN-only layout: VOR slot [32:51] blank, position in the second slot
ADK_TAC = "SCAND        ADK   PA011400 DUW                    ADK N51521587W176402739E0070003291     NARMOUNT MOFFETT                 002422105"
AA_NDB = "SUSADB       AA    K3003650HOLW N47003259W096485466                       E0040           NARKENIE                         268431805"
CAMRN = "SUSAEAENRT   CAMRN K60    C   B N40010229W073513981                       W0124     NAR           CAMRN                    301322605"
BETTE = "SUSAEAENRT   BETTE K60    C     N40333326W073004221                       W0129     NAR           BETTE                    289062504"
LENDY = "SUSAEAENRT   LENDY K60    R     N40545341W074080693                       W0125     NAR           LENDY                    429372504"
ROBER = "SUSAEAENRT   ROBER K60    W     N40410767W073015740                       W0129     NAR           ROBER                    484102504"

APT_JFK = "SUSAP KJFKK6AJFK     0     145YHN40382374W073464329W013000013         1800018000C    MNAR    JOHN F KENNEDY INTL           303921912"
APT_BOS = "SUSAP KBOSK6ABOS     0     100YHN42214660W071002300W015000019         1800018000C    MNAR    GENERAL EDWARD LAWRENCE LOGAN 388731711"
RWY_04L = "SUSAP KJFKK6GRW04L   0120790440 N40372318W073470505         -0028300012046057200IIHIQ1                                     308531709"
RWY_13R = "SUSAP KJFKK6GRW13R   0145111340 N40384378W073483739         -0028100013204455200R                                          308562506"
TW_AROKE = "SUSAP KJFKK6CAROKE K60    R     N40282056W073540760                       W0125     NAR           AROKE                    303932504"
RWY_EGE25 = "SUSAP KEGEK2GRW25    0090002530 N39383878W106541310         +1979606540100055150IIESJL                                     822302504"  # LDA, non-numeric category code

HDR = "HDR01FAACIFP18      001P013203968112609  12-AUG-202618:29:21  U.S.A. DOT FAA"
UNHANDLED = "SUSAUCK1ACYVR PAC  A00100     G N49000000W123192000                              02500M12500MVANCOUVER"  # controlled airspace, not in slice

# KJFK ILS RWY 04R -- IF ZETAL, CF EBBEE (FAF), CF RW04R (MAP), then missed approach
I04R = [
    "SUSAP KJFKK6FI04R  I      010ZETALK6PC0E  I    IF IJFKK6      22380110        PI  J 020000150018000                 0 NS   305851808",
    "SUSAP KJFKK6FI04R  I      020EBBEEK6PC0E  F    CF IJFKK6      2238006104400049PI  H 0150001500            JFK   K6D 0 NS   305862006",
    "SUSAP KJFKK6FI04R  I      030RW04RK6PG0GY M    CF IJFKK6      2238001604400045PI    00065             -300          0 NS   305871808",
    "SUSAP KJFKK6FI04R  I      040         0  M     CA                     0438        + 00800                           0 NS   305881808",
    "SUSAP KJFKK6FI04R  I      050         0        VI                     0990                                          0 NS   305891808",
    "SUSAP KJFKK6FI04R  I      060DPK  K6D 0VY      CF DPK K6      0000000004100080D   + 04000                           0 NS   305901212",
    "SUSAP KJFKK6FI04R  I      070DPK  K6D 0VE  L   HM                     2581T010    + 04000                           0 NS   305911709",
]
# KJFK CAMRN FIVE arrival, transition "ALL"
CAMRN5 = [
    "SUSAP KJFKK6ECAMRN52ALL   010SIE  K6D 0V       IF                                             18000                        304832506",
    "SUSAP KJFKK6ECAMRN52ALL   020BOTONK6PC0E       TF                                                                          304842506",
    "SUSAP KJFKK6ECAMRN52ALL   030HOGGSK6EA0E       TF                                                                          304852506",
]

# V16 through the LA basin: LAX VOR - DODGR - LAHAB - PRADO - WISUP
V16 = [
    "SUSAER       V16         0100LAX  K2D 0V    OL                        07590147     04000     17500                         648592405",
    "SUSAER       V16         0110DODGRK2EA0E    OL                        075901060764 04000     17500                         648602405",
    "SUSAER       V16         0120LAHABK2EA0E    OL                        075900710764 04000     17500                         648612405",
    "SUSAER       V16         0130PRADOK2EA0E    OL                        075900320764 05000     17500                         648622405",
    "SUSAER       V16         0140WISUPK2EA0E    OL                        075900940764 05000     17500                         648632405",
]

ALL = [
    HDR, "",
    JFK_VOR, DPK_VOR, HTO_VOR, ADK_TAC, AA_NDB,
    CAMRN, BETTE, LENDY, ROBER,
    APT_JFK, APT_BOS, RWY_04L, RWY_13R, TW_AROKE,
    *I04R, *CAMRN5,
    *V16,
    UNHANDLED,
]


@pytest.fixture
def db():
    return parse_cifp(ALL)


# --------------------------------------------------------------------------- #
# counts / dispatch                                                          #
# --------------------------------------------------------------------------- #
def test_counts(db):
    assert db.counts() == {
        "waypoints": 5,   # 4 enroute + 1 terminal (AROKE)
        "vhf": 4,          # JFK, DPK, HTO, ADK
        "ndb": 1,
        "airports": 2,
        "runways": 2,      # both attached to KJFK
        "procedures": 2,   # I04R approach + CAMRN5 STAR
        "airways": 1,      # V16
        "airport_comms": 0,  # NASR merge not run in this fixture
    }
    assert db.source == "FAA CIFP"
    assert db.notes == []  # HDR / blank / unhandled sections are ignored, not "errors"


def test_len(db):
    assert len(db) == 12  # waypoints 5 + vhf 4 + ndb 1 + airports 2 (procedures not counted)


# --------------------------------------------------------------------------- #
# VHF navaids                                                                #
# --------------------------------------------------------------------------- #
def test_jfk_vor_fields(db):
    jfk = db.vhf["JFK"][0]
    assert jfk.ident == "JFK"
    assert jfk.region == "K6"
    assert jfk.freq_mhz == pytest.approx(115.9)
    assert jfk.name == "KENNEDY"
    assert jfk.nav_class == "VDHW"
    assert jfk.has_dme
    assert jfk.magvar_deg == pytest.approx(-12.0)
    assert jfk.pos.lat == pytest.approx(40.632883, abs=1e-6)
    assert jfk.pos.lon == pytest.approx(-73.771392, abs=1e-6)


def test_dpk_has_elevation(db):
    dpk = db.vhf["DPK"][0]
    assert dpk.freq_mhz == pytest.approx(117.7)
    assert dpk.elev_ft == 117
    assert dpk.name == "DEER PARK"


def test_hto_tacan_counts_as_dme(db):
    hto = db.vhf["HTO"][0]
    assert hto.nav_class == "VTHW"
    assert hto.has_dme  # 'T'
    assert hto.magvar_deg == pytest.approx(-13.0)


def test_dme_only_layout_uses_second_position_slot():
    db = parse_cifp([ADK_TAC])
    adk = db.vhf["ADK"][0]
    assert adk.region == "PA"
    assert adk.freq_mhz == pytest.approx(114.0)
    assert adk.nav_class == "DUW"
    assert adk.has_dme
    assert adk.elev_ft == 329
    assert adk.name == "MOUNT MOFFETT"
    assert adk.pos.lat == pytest.approx(51.871075, abs=1e-6)
    assert adk.pos.lon == pytest.approx(-176.674275, abs=1e-6)


# --------------------------------------------------------------------------- #
# NDB                                                                        #
# --------------------------------------------------------------------------- #
def test_aa_ndb_fields(db):
    aa = db.ndb["AA"][0]
    assert aa.ident == "AA"
    assert aa.region == "K3"
    assert aa.freq_khz == pytest.approx(365.0)
    assert aa.name == "KENIE"
    assert aa.magvar_deg == pytest.approx(4.0)
    assert aa.pos.lat == pytest.approx(47.009053, abs=1e-6)
    assert aa.pos.lon == pytest.approx(-96.815183, abs=1e-6)


# --------------------------------------------------------------------------- #
# waypoints                                                                  #
# --------------------------------------------------------------------------- #
def test_camrn_waypoint(db):
    wp = db.waypoints["CAMRN"][0]
    assert wp.ident == "CAMRN"
    assert wp.region == "K6"
    assert wp.name == "CAMRN"
    assert wp.pos.lat == pytest.approx(40.017303, abs=1e-6)
    assert wp.pos.lon == pytest.approx(-73.861058, abs=1e-6)


def test_all_ny_waypoints_present(db):
    assert set(db.waypoints) == {"CAMRN", "BETTE", "LENDY", "ROBER", "AROKE"}


def test_terminal_waypoint_kind_and_position(db):
    aroke = db.waypoints["AROKE"][0]
    assert aroke.kind == "terminal"
    assert aroke.region == "K6"
    assert aroke.pos.lat == pytest.approx(40.472378, abs=1e-6)
    assert aroke.pos.lon == pytest.approx(-73.902111, abs=1e-6)


# --------------------------------------------------------------------------- #
# airports + runways                                                         #
# --------------------------------------------------------------------------- #
def test_airport_fields(db):
    jfk = db.airport("kjfk")  # case-insensitive
    assert jfk.ident == "KJFK"
    assert jfk.iata == "JFK"
    assert jfk.region == "K6"
    assert jfk.name == "JOHN F KENNEDY INTL"
    assert jfk.elev_ft == 13
    assert jfk.magvar_deg == pytest.approx(-13.0)
    assert jfk.longest_runway_ft == 14500
    assert jfk.transition_alt_ft == 18000
    assert jfk.pos.lat == pytest.approx(40.639928, abs=1e-6)
    assert jfk.pos.lon == pytest.approx(-73.778692, abs=1e-6)


def test_bos_airport(db):
    bos = db.airport("KBOS")
    assert bos.name == "GENERAL EDWARD LAWRENCE LOGAN"
    assert bos.elev_ft == 19
    assert bos.magvar_deg == pytest.approx(-15.0)
    assert bos.longest_runway_ft == 10000
    assert bos.runways == {}  # no P/G fixtures for BOS


def test_runways_attached_to_airport(db):
    jfk = db.airport("KJFK")
    assert set(jfk.runways) == {"RW04L", "RW13R"}

    r04l = jfk.runways["RW04L"]
    assert r04l.number == "04L"
    assert r04l.length_ft == 12079
    assert r04l.width_ft == 200
    assert r04l.bearing_deg == pytest.approx(44.0)
    assert r04l.threshold.lat == pytest.approx(40.623106, abs=1e-6)
    assert r04l.threshold.lon == pytest.approx(-73.784736, abs=1e-6)
    assert r04l.has_ils
    assert r04l.ils_ident == "IHIQ"
    assert r04l.ils_category == 1


def test_runway_without_ils(db):
    r13r = db.airport("KJFK").runways["RW13R"]
    assert r13r.length_ft == 14511
    assert r13r.bearing_deg == pytest.approx(134.0)
    assert not r13r.has_ils
    assert r13r.ils_ident == ""
    assert r13r.ils_category == 0


def test_runway_with_non_numeric_ils_category():
    # LDA runways carry a letter in the category column; must not crash
    d = parse_cifp([APT_JFK.replace("KJFK", "KEGE"), RWY_EGE25])
    r25 = d.airport("KEGE").runways["RW25"]
    assert r25.length_ft == 9000
    assert r25.bearing_deg == pytest.approx(253.0)  # RW25 -> ~253 deg
    assert r25.has_ils and r25.ils_ident == "IESJ"
    assert r25.ils_category == 0
    assert d.notes == []


# verbatim CIFP section P·I record (KLNS ILS RWY 08)
LOC_KLNS = "SUSAP KLNSK6IILNS1   010870RW08 N40073972W0761644650770N40071355W0761800010600 07560546300W00904000395                     449161207"


def test_localizer_from_section_pi():
    d = parse_cifp([LOC_KLNS])
    assert d.counts()["vhf"] == 1
    loc = d.vhf["ILNS"][0]
    assert loc.freq_mhz == pytest.approx(108.70)
    assert loc.is_localizer and loc.nav_class == "ILSW"
    assert loc.loc_bearing_deg == pytest.approx(77.0)
    assert loc.runway_ident == "RW08" and loc.airport_ident == "KLNS"
    assert loc.ils_category == 1
    assert loc.pos.lat == pytest.approx(40.1277, abs=1e-3)


def test_section_pi_localizer_replaces_a_co_located_section_d_copy():
    # a section-D localizer for the same facility, ~same spot -> P·I wins
    sec_d = ("SUSAD        ILNS  K6010870ITW  N40073900W076164400    "
             "                   W0090000395     NARILS RWY 08                      000012609")
    d = parse_cifp([sec_d, LOC_KLNS])
    assert len(d.vhf["ILNS"]) == 1
    assert d.vhf["ILNS"][0].loc_bearing_deg == pytest.approx(77.0)   # the P·I one


def test_orphan_runway_is_noted():
    # runway for KZZZ, no matching P/A record
    orphan = RWY_04L.replace("KJFK", "KZZZ")
    d = parse_cifp([orphan])
    assert d.counts()["runways"] == 0
    assert any("unknown airport" in n for n in d.notes)


def test_find_includes_airport(db):
    hits = db.find("KJFK")
    assert len(hits) == 1
    assert hits[0].__class__.__name__ == "Airport"


# --------------------------------------------------------------------------- #
# procedures                                                                 #
# --------------------------------------------------------------------------- #
from navdata.model import LegType  # noqa: E402


def test_procedure_grouping(db):
    assert {p.ident for p in db.approaches("KJFK")} == {"I04R"}
    assert {p.ident for p in db.stars("KJFK")} == {"CAMRN5"}
    assert db.sids("KJFK") == []
    assert db.procedure("KJFK", "i04r").kind == "approach"


def test_approach_legs_in_order_and_typed(db):
    ap = db.procedure("KJFK", "I04R")
    assert ap.route_type == "I"
    assert ap.transition_names() == []          # all legs in the common segment
    legs = ap.assemble()
    assert [leg.seq for leg in legs] == [10, 20, 30, 40, 50, 60, 70]
    assert [leg.leg_type for leg in legs] == [
        LegType.IF, LegType.CF, LegType.CF, LegType.CA, LegType.VI, LegType.CF, LegType.HM
    ]


def test_approach_fix_and_navaid_refs(db):
    legs = {leg.seq: leg for leg in db.procedure("KJFK", "I04R").assemble()}

    zetal = legs[10]
    assert zetal.fix_ident == "ZETAL" and zetal.fix_region == "K6"
    assert zetal.recnav_ident == "IJFK"
    assert zetal.theta == pytest.approx(223.8)
    assert zetal.rho == pytest.approx(11.0)
    assert zetal.alt1_ft == 2000            # descriptor "J" (glidepath intercept)

    ebbee = legs[20]
    assert ebbee.is_faf and not ebbee.is_map
    assert ebbee.course_mag == pytest.approx(44.0)
    assert ebbee.distance_nm == pytest.approx(4.9)
    assert ebbee.alt_desc == "H" and ebbee.alt1_ft == 1500

    assert legs[30].is_map and legs[30].fix_ident == "RW04R"
    assert not legs[40].is_map               # a missed-approach CA leg is not the MAP
    assert legs[60].is_flyover               # DPK, "VY" description


def test_hold_leg_uses_time_not_distance(db):
    hm = db.procedure("KJFK", "I04R").assemble()[-1]
    assert hm.leg_type == LegType.HM
    assert hm.fix_ident == "DPK"
    assert hm.turn == "L"
    assert hm.course_mag == pytest.approx(258.1)
    assert hm.time_min == pytest.approx(1.0)
    assert hm.distance_nm is None


def test_star_transition_assembly(db):
    star = db.procedure("KJFK", "CAMRN5")
    assert star.kind == "star"
    assert star.transition_names() == ["ALL"]
    legs = star.assemble("ALL")
    assert [leg.fix_ident for leg in legs] == ["SIE", "BOTON", "HOGGS"]
    assert legs[0].leg_type == LegType.IF
    assert legs[0].alt1_ft is None           # 18000 there is the transition altitude, not a constraint


def test_legtype_parse_unknown_passthrough():
    assert LegType.parse("CF") == LegType.CF
    assert LegType.parse("ZZ") == "ZZ"
    assert LegType.parse("  ") == ""


def test_continuation_records_are_skipped(db):
    # a procedure record with no path terminator carries only supplementary data
    cont = I04R[1][:47] + "  " + I04R[1][49:]
    d = parse_cifp([APT_JFK, *I04R[:1], cont])
    assert len(d.procedure("KJFK", "I04R").assemble()) == 1  # only the IF leg


# --------------------------------------------------------------------------- #
# airways                                                                    #
# --------------------------------------------------------------------------- #
def test_airway_points_in_sequence(db):
    v16 = db.airway("v16")
    assert v16.ident == "V16"
    assert v16.fix_idents() == ["LAX", "DODGR", "LAHAB", "PRADO", "WISUP"]
    assert [p.seq for p in v16.points] == [100, 110, 120, 130, 140]


def test_airway_point_fields(db):
    p = db.airway("V16").points[1]  # DODGR
    assert p.fix_ident == "DODGR" and p.fix_region == "K2" and p.fix_section == "E"
    assert p.course_mag == pytest.approx(75.9)
    assert p.distance_nm == pytest.approx(10.6)
    assert p.min_alt_ft == 4000
    assert p.max_alt_ft == 17500


def test_airway_first_point_is_navaid(db):
    lax = db.airway("V16").points[0]
    assert lax.fix_ident == "LAX" and lax.fix_section == "D"


def test_airway_segment_forward_and_reverse(db):
    v16 = db.airway("V16")
    fwd = v16.segment("DODGR", "PRADO")
    assert [p.fix_ident for p in fwd] == ["DODGR", "LAHAB", "PRADO"]
    rev = v16.segment("PRADO", "DODGR")
    assert [p.fix_ident for p in rev] == ["PRADO", "LAHAB", "DODGR"]
    assert v16.segment("DODGR", "NOPE") == []


def test_records_out_of_file_order_still_sort(db):
    shuffled = [V16[3], V16[0], V16[4], V16[1], V16[2]]
    d = parse_cifp(shuffled)
    assert d.airway("V16").fix_idents() == ["LAX", "DODGR", "LAHAB", "PRADO", "WISUP"]


# --------------------------------------------------------------------------- #
# lookup                                                                     #
# --------------------------------------------------------------------------- #
def test_find_prefers_navaid_then_ndb_then_fix(db):
    hits = db.find("JFK")
    assert len(hits) == 1 and hits[0].name == "KENNEDY"
    assert db.find("nonesuch") == []


def test_nearest_fix_by_distance(db):
    # two entries share ident "AA" is not in fixture; use CAMRN vs a far ref
    from navmath import Point

    near = db.nearest_fix("CAMRN", Point(40.0, -73.9))
    assert near is not None and near.ident == "CAMRN"


# --------------------------------------------------------------------------- #
# area filter + error handling                                               #
# --------------------------------------------------------------------------- #
def test_area_filter_excludes_non_us():
    eeu = "SEEU" + CAMRN[4:]  # same record, European area code
    kept = parse_cifp([CAMRN, eeu])
    assert kept.counts()["waypoints"] == 2
    us_only = parse_cifp([CAMRN, eeu], areas={"USA"})
    assert us_only.counts()["waypoints"] == 1


def test_malformed_record_skipped_and_noted():
    broken = JFK_VOR.replace("N40375838", "NXX375838")
    db = parse_cifp([broken, DPK_VOR])
    assert db.counts()["vhf"] == 1  # only DPK survived
    assert db.notes and "1 record(s) skipped" in db.notes[0]


def test_strict_reraises():
    broken = JFK_VOR.replace("N40375838", "NXX375838")
    with pytest.raises(ValueError):
        parse_cifp([broken], strict=True)


# --------------------------------------------------------------------------- #
# SBAS path points and level of service (section P.P / the "W" continuation on the FAF leg)
# --------------------------------------------------------------------------- #
PP_R23Z = ("SUSAP KFDKK6PR23-Z RW23 001Z0000W23A0N3925138000W07722063800+005340300"
           "N3924225100W07723028400106750184000500F4005002473B822894260804")
SVC_Z = "SUSAP KFDKK6FR23-Z R      020SHUEYK6PC2WALPV       ALNAV/VNAV ALNAV                                                   JS   894141310"
SVC_Y = "SUSAP KFDKK6FR23-Y R      020SHUEYK6PC2WN          N          ALNAV                                                   PS   894002207"
RWY_23 = "SUSAP KFDKK6GRW23    0058192280 N39251380W077220638         +0053500283000054100IIFDK1                                     894232207"


def test_path_point_record_gives_the_glidepath_geometry():
    """Offsets checked offline against X-Plane's own rows for 4711 approaches: LTP and TCH agree for
    99%, the rest are amendments between the 2406 and 2609 cycles."""
    from navdata.cifp import _path_point_from_line
    pp = _path_point_from_line(PP_R23Z)
    assert (pp.airport, pp.approach, pp.runway, pp.ref_path_id) == ("KFDK", "R23-Z", "RW23", "W23A")
    assert pp.ltp.lat == pytest.approx(39.4205) and pp.ltp.lon == pytest.approx(-77.368439, abs=1e-5)
    assert pp.gpa_deg == 3.0 and pp.tch_ft == 50.0 and pp.course_width_m == pytest.approx(106.75)
    assert pp.fpap.lat == pytest.approx(39.406253, abs=1e-5)


def test_level_of_service_comes_from_the_faf_continuation_and_dispatches_into_the_db():
    db = parse_cifp([PP_R23Z, SVC_Z, SVC_Y, RWY_23])
    assert db.path_points[("KFDK", "R23-Z")].gpa_deg == 3.0
    assert db.approach_service[("KFDK", "R23-Z")] == frozenset({"LPV", "LNAV/VNAV", "LNAV"})
    assert db.approach_service[("KFDK", "R23-Y")] == frozenset({"LNAV"})        # -Y: no LPV / VNAV
