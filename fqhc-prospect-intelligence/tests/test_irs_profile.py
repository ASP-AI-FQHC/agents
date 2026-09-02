"""What a Form 990 says about the filer itself, beyond Part VII.

The fixture below is a cut-down but structurally faithful return: the same
element names, nesting and namespace the IRS uses, including the traps that
matter -- the three functional-expense column names repeating on every Part IX
line, and Part III's very generic ``Desc`` element.
"""

from __future__ import annotations

import pytest

from pipeline.irs import (
    OrganizationFacts,
    latest_matching,
    parse_return,
    tri_flag,
)

FULL_RETURN = """<?xml version="1.0" encoding="UTF-8"?>
<Return xmlns="http://www.irs.gov/efile" returnVersion="2023v4.0">
  <ReturnHeader>
    <TaxYr>2023</TaxYr>
    <Filer><EIN>363197647</EIN></Filer>
  </ReturnHeader>
  <ReturnData>
    <IRS990>
      <ActivityOrMissionDesc>To provide accessible primary health care to
        medically underserved communities.</ActivityOrMissionDesc>
      <FormationYr>1982</FormationYr>
      <LegalDomicileStateCd>IL</LegalDomicileStateCd>
      <WebsiteAddressTxt>www.example-health.org</WebsiteAddressTxt>
      <TotalEmployeeCnt>262</TotalEmployeeCnt>
      <TotalVolunteersCnt>45</TotalVolunteersCnt>

      <CYTotalRevenueAmt>28100000</CYTotalRevenueAmt>
      <CYTotalExpensesAmt>33800000</CYTotalExpensesAmt>
      <TotalAssetsEOYAmt>40100000</TotalAssetsEOYAmt>
      <TotalLiabilitiesEOYAmt>8000000</TotalLiabilitiesEOYAmt>
      <NetAssetsOrFundBalancesEOYAmt>32100000</NetAssetsOrFundBalancesEOYAmt>
      <CYSalariesCompEmpBnftPaidAmt>21400000</CYSalariesCompEmpBnftPaidAmt>
      <CYGrantsAndSimilarPaidAmt>50000</CYGrantsAndSimilarPaidAmt>

      <!-- Part IX. The column names repeat on every line; only the totals
           group carries the figures we want. -->
      <GrantsToDomesticOrgsGrp>
        <TotalAmt>50000</TotalAmt>
        <ProgramServicesAmt>50000</ProgramServicesAmt>
      </GrantsToDomesticOrgsGrp>
      <OfficerCompensationGrp>
        <TotalAmt>1900000</TotalAmt>
        <ProgramServicesAmt>400000</ProgramServicesAmt>
        <ManagementAndGeneralAmt>1500000</ManagementAndGeneralAmt>
      </OfficerCompensationGrp>
      <TotalFunctionalExpensesGrp>
        <TotalAmt>33800000</TotalAmt>
        <ProgramServicesAmt>29100000</ProgramServicesAmt>
        <ManagementAndGeneralAmt>4400000</ManagementAndGeneralAmt>
        <FundraisingAmt>300000</FundraisingAmt>
      </TotalFunctionalExpensesGrp>

      <!-- Part XII -->
      <FSAuditedInd>true</FSAuditedInd>
      <FederalGrantAuditRequiredInd>true</FederalGrantAuditRequiredInd>
      <FederalGrantAuditPerformedInd>true</FederalGrantAuditPerformedInd>
      <AuditCommitteeInd>false</AuditCommitteeInd>

      <!-- Part III -->
      <ProgramSrvcAccomplishmentGrp>
        <ActivityCd>001</ActivityCd>
        <ExpenseAmt>24000000</ExpenseAmt>
        <GrantAmt>0</GrantAmt>
        <RevenueAmt>19000000</RevenueAmt>
        <Desc>Primary medical care. The organization operated eight health
          centers providing family practice, internal medicine and pediatric
          care to 84,000 patients.</Desc>
      </ProgramSrvcAccomplishmentGrp>
      <ProgramSrvcAccomplishmentGrp>
        <ExpenseAmt>4100000</ExpenseAmt>
        <RevenueAmt>2600000</RevenueAmt>
        <Desc>Dental services provided at four sites.</Desc>
      </ProgramSrvcAccomplishmentGrp>

      <!-- Part VII, so the return is usable by the existing stage too -->
      <Form990PartVIISectionAGrp>
        <PersonNm>BERNEICE MILLS-THOMAS</PersonNm>
        <TitleTxt>PRESIDENT AND CEO</TitleTxt>
        <OfficerInd>X</OfficerInd>
        <ReportableCompFromOrgAmt>410000</ReportableCompFromOrgAmt>
      </Form990PartVIISectionAGrp>
    </IRS990>
  </ReturnData>
</Return>"""


@pytest.fixture
def parsed():
    return parse_return(FULL_RETURN)


def test_the_filers_own_description_is_kept_verbatim(parsed) -> None:
    assert parsed.facts.mission.startswith("To provide accessible primary")
    # Whitespace from the filer's word processor is collapsed; wording is not.
    assert "\n" not in parsed.facts.mission
    assert "medically underserved communities." in parsed.facts.mission


def test_identity_and_size_are_read(parsed) -> None:
    facts = parsed.facts
    assert facts.formation_year == 1982
    assert facts.domicile_state == "IL"
    assert facts.employee_count == 262
    assert facts.volunteer_count == 45
    assert facts.website == "www.example-health.org"


def test_the_balance_sheet_is_complete(parsed) -> None:
    facts = parsed.facts
    assert facts.total_revenue == 28_100_000
    assert facts.total_expenses == 33_800_000
    assert facts.total_assets == 40_100_000
    assert facts.total_liabilities == 8_000_000
    assert facts.net_assets == 32_100_000
    assert facts.has_balance_sheet


def test_functional_expenses_come_from_the_totals_row_not_a_line_item(
    parsed,
) -> None:
    """The regression that matters: the column names repeat on every line."""
    facts = parsed.facts
    assert facts.program_expenses == 29_100_000
    assert facts.management_expenses == 4_400_000
    assert facts.fundraising_expenses == 300_000
    # Not the grants line (50,000) nor the officer line (400,000).
    assert facts.program_expenses not in (50_000, 400_000)


def test_audit_answers_distinguish_no_from_unanswered(parsed) -> None:
    facts = parsed.facts
    assert facts.financials_audited is True
    assert facts.single_audit_required is True
    assert facts.single_audit_performed is True
    # Answered "no" -- which is a fact, and is not the same as absent.
    assert facts.audit_committee is False


def test_an_unanswered_checkbox_is_none_not_false() -> None:
    xml = """<Return xmlns="http://www.irs.gov/efile"><ReturnData><IRS990>
      <FSAuditedInd>0</FSAuditedInd>
    </IRS990></ReturnData></Return>"""
    facts = parse_return(xml).facts
    assert facts.financials_audited is False
    assert facts.single_audit_required is None
    assert facts.audit_committee is None


def test_programs_are_read_in_the_order_filed(parsed) -> None:
    programs = parsed.programs
    assert len(programs) == 2
    assert programs[0].expenses == 24_000_000
    assert programs[0].revenue == 19_000_000
    assert "Primary medical care" in programs[0].description
    assert programs[1].description == "Dental services provided at four sites."


def test_a_program_gets_a_short_title_from_its_own_first_sentence(parsed) -> None:
    assert parsed.programs[0].headline.startswith("Primary medical care.")
    assert len(parsed.programs[0].headline) < len(parsed.programs[0].description)


def test_a_zero_grant_is_kept_as_zero_not_dropped(parsed) -> None:
    """The filer reported $0 of grants; that is different from not reporting."""
    assert parsed.programs[0].grants == 0
    assert parsed.programs[1].grants is None


def test_an_implausible_formation_year_is_rejected() -> None:
    xml = """<Return xmlns="http://www.irs.gov/efile"><ReturnData><IRS990>
      <FormationYr>19822</FormationYr>
    </IRS990></ReturnData></Return>"""
    assert parse_return(xml).facts.formation_year is None


def test_a_return_with_nothing_in_it_reports_nothing(parsed) -> None:
    empty = parse_return(
        """<Return xmlns="http://www.irs.gov/efile"><ReturnData><IRS990EZ/>
        </ReturnData></Return>"""
    )
    assert empty.facts.has_any is False
    assert parsed.facts.has_any is True


def test_each_section_comes_from_the_newest_return_that_has_it() -> None:
    """A thin recent return must not discard a fuller older one."""
    newest = parse_return(
        """<Return xmlns="http://www.irs.gov/efile">
          <ReturnHeader><TaxYr>2024</TaxYr></ReturnHeader>
          <ReturnData><IRS990><TotalEmployeeCnt>270</TotalEmployeeCnt>
          </IRS990></ReturnData></Return>"""
    )
    older = parse_return(FULL_RETURN)

    facts_source = latest_matching([older, newest], lambda r: r.facts.has_any)
    programs_source = latest_matching([older, newest], lambda r: bool(r.programs))

    assert facts_source.tax_year == 2024
    assert programs_source.tax_year == 2023  # the newer return has no Part III


def test_tri_flag_reads_the_three_states() -> None:
    import xml.etree.ElementTree as ElementTree

    root = ElementTree.fromstring("<a><yes>1</yes><no>0</no></a>")
    assert tri_flag(root, "yes") is True
    assert tri_flag(root, "no") is False
    assert tri_flag(root, "absent") is None


def test_facts_default_to_all_unknown() -> None:
    facts = OrganizationFacts()
    assert facts.has_any is False
    assert facts.has_balance_sheet is False
    assert facts.has_expense_split is False


# ---------------------------------------------------------------------------
# Names as filers actually type them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filed, expected",
    [
        # Part VII has no column for "served part of the year", so filers put
        # it in the name box. All of these are real shapes.
        ("DANIEL FULWILER TERM 6225", "DANIEL FULWILER"),
        ("JANE SMITH (THRU 6/30)", "JANE SMITH"),
        ("A RUIZ - RESIGNED", "A RUIZ"),
        ("MARY JONES, PARTIAL YEAR", "MARY JONES"),
        ("JOHN DOE EFFECTIVE 1/1/2024", "JOHN DOE"),
        ("LEE ADAMS RETIRED", "LEE ADAMS"),
        ("PAT LOWE [OUTGOING]", "PAT LOWE"),
    ],
)
def test_a_filers_term_note_is_not_part_of_the_name(filed, expected) -> None:
    from pipeline.irs import clean_person_name

    assert clean_person_name(filed) == expected


@pytest.mark.parametrize(
    "name",
    [
        "BERNEICE MILLS-THOMAS",
        "Tristé Lieteau Smith, MD, JD",
        "SEAN O'BRIEN MD",
        "ELENA RUIZ-GARCIA",
        "JAMES T CARRINGTON",
        "ANNA VAN DER BERG",
        "Kiran Siddiqui, M.Ed, LPC",
        # Words that merely start with a marker must survive intact.
        "MARTIN LUTHER KING III",
        "ENDERBY WELLS",
        "TERMAINE ENDICOTT",
    ],
)
def test_a_real_name_is_left_exactly_as_filed(name) -> None:
    from pipeline.irs import clean_person_name

    assert clean_person_name(name) == name


def test_a_name_is_never_emptied_by_cleaning() -> None:
    """An odd name is still a name; a blank row is not an improvement."""
    from pipeline.irs import clean_person_name

    assert clean_person_name(None) is None
    assert clean_person_name("   ") is None
    assert clean_person_name("(RESIGNED)") == "(RESIGNED)"


def test_the_cleaning_runs_on_the_way_out_of_a_return() -> None:
    xml = """<Return xmlns="http://www.irs.gov/efile"><ReturnData><IRS990>
      <Form990PartVIISectionAGrp>
        <PersonNm>DANIEL FULWILER TERM 6225</PersonNm>
        <TitleTxt>CHIEF EXECUTIVE OFFICER</TitleTxt>
        <OfficerInd>X</OfficerInd>
      </Form990PartVIISectionAGrp>
    </IRS990></ReturnData></Return>"""

    person = parse_return(xml).people[0]
    assert person.name == "DANIEL FULWILER"
    assert person.title == "CHIEF EXECUTIVE OFFICER"
