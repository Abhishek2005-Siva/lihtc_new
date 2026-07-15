"""
ingest_AMI.py — Download all HUD AMI income limit files (Excel + PDF) for 2010-2025.

Dataset keys:
  Primary data (processed by silver_ami.py):
    section8          — Section 8 income limits Excel
    section235_236    — Section 235/236 BMIR income limits Excel
    npg               — County→area crosswalk Excel (2015+)
    national_nonmet   — National non-metro floor Excel (2023+)

  Reference PDFs (archived as bronze; not processed by silver):
    hud_sec8_pdf          — HUD Section 8 notice/summary (short)
    hud_sec236_pdf        — HUD Section 236 notice/summary (short)
    section8_il_pdf       — Full Section 8 income limits PDF
    section8_pdf          — Full Section 8 income limits PDF (pre-2015 naming alias)
    section235_236_il_pdf — Full Section 236 BMIR income limits PDF
    section235_236_pdf    — Full Section 236 BMIR PDF (pre-2015 naming alias)
    area_definitions_pdf  — Area definitions
    briefing_material_pdf — Briefing material or methodology PDF
    medians_pdf           — Median family incomes PDF (Medians{YYYY} or msacounty for 2010)
    medians_main_pdf      — Primary Medians{YYYY}.pdf (2010 has two median files)
    medians_methodology_pdf — Medians methodology (2018+)
    medians_notice_pdf    — Medians FY notice (2022+)
    medians_attachment_pdf — State medians attachment (2023+)
    state_il_pdf          — State income limits and median family incomes report
    income_limits_30_pdf  — HUD 30% income limit for all areas (2015+)

Usage:
    python scripts/ingest/ingest_AMI.py --year 2025
    python scripts/ingest/ingest_AMI.py --year 2010-2025
    python scripts/ingest/ingest_AMI.py --year 2010-2014 2025
"""
import argparse
import requests
from pathlib import Path

import _logbook

_ROOT = Path(__file__).resolve().parents[2]
B = "https://www.huduser.gov/portal/datasets/il/il{yy}"

# ---------------------------------------------------------------------------
# Explicit per-year URL maps (None = not published by HUD that year)
# Keys present in all years; omitted keys default to None.
# ---------------------------------------------------------------------------
_YEAR_URLS: dict[int, dict[str, str | None]] = {

    2010: {
        "section8":               B.format(yy="10") + "/Section8.xls",
        "section235_236":         None,
        "npg":                    None,
        "national_nonmet":        None,
        "hud_sec8_pdf":           B.format(yy="10") + "/HUD-sec8.pdf",
        "hud_sec236_pdf":         B.format(yy="10") + "/HUD-sec236.pdf",
        "section8_pdf":           B.format(yy="10") + "/IncomeLimits_Section8.pdf",
        "section235_236_pdf":     B.format(yy="10") + "/IncomeLimits_Section236_BMIR.pdf",
        "section8_il_pdf":        None,
        "section235_236_il_pdf":  None,
        "area_definitions_pdf":   B.format(yy="10") + "/area_definitions.pdf",
        "briefing_material_pdf":  B.format(yy="10") + "/IncomeLimitsBriefingMaterial_FY10.pdf",
        "medians_pdf":            B.format(yy="10") + "/msacounty_medians.pdf",
        "medians_main_pdf":       B.format(yy="10") + "/Medians2010.pdf",
        "medians_methodology_pdf": None,
        "medians_notice_pdf":     None,
        "medians_attachment_pdf": None,
        "state_il_pdf":           B.format(yy="10") + "/State_Incomelimits_Report.pdf",
        "income_limits_30_pdf":   None,
    },

    2011: {
        "section8":               B.format(yy="11") + "/Section8_v3.xls",
        "section235_236":         B.format(yy="11") + "/S235_S236_v3.xls",
        "npg":                    None,
        "national_nonmet":        None,
        "hud_sec8_pdf":           B.format(yy="11") + "/HUD_Sec8_11_sig.pdf",
        "hud_sec236_pdf":         B.format(yy="11") + "/HUD_sec236_11_sig.pdf",
        "section8_pdf":           B.format(yy="11") + "/IncomeLimits_Section8_v3.pdf",
        "section235_236_pdf":     B.format(yy="11") + "/IncomeLImits_Section236_BMIR_v3.pdf",
        "section8_il_pdf":        None,
        "section235_236_il_pdf":  None,
        "area_definitions_pdf":   B.format(yy="11") + "/area_definitions.pdf",
        "briefing_material_pdf":  B.format(yy="11") + "/IncomeLimitsBriefingMaterial_FY11_v3.pdf",
        "medians_pdf":            B.format(yy="11") + "/medians2011_sig.pdf",
        "medians_main_pdf":       None,
        "medians_methodology_pdf": None,
        "medians_notice_pdf":     None,
        "medians_attachment_pdf": None,
        "state_il_pdf":           B.format(yy="11") + "/state_incomelimits_report.pdf",
        "income_limits_30_pdf":   None,
    },

    2012: {
        "section8":               B.format(yy="12") + "/Section8.xls",
        "section235_236":         B.format(yy="12") + "/S235_S236.xls",
        "npg":                    None,
        "national_nonmet":        None,
        "hud_sec8_pdf":           B.format(yy="12") + "/HUD_sec8_12.pdf",
        "hud_sec236_pdf":         B.format(yy="12") + "/HUD_sec236_12.pdf",
        "section8_pdf":           B.format(yy="12") + "/IncomeLimits_Section8.pdf",
        "section235_236_pdf":     B.format(yy="12") + "/IncomeLimits_Section236_BMIR.pdf",
        "section8_il_pdf":        None,
        "section235_236_il_pdf":  None,
        "area_definitions_pdf":   B.format(yy="12") + "/area_definitions.pdf",
        "briefing_material_pdf":  B.format(yy="12") + "/IncomeLimitsBriefingMaterial_FY12_v2.pdf",
        "medians_pdf":            B.format(yy="12") + "/Medians2012.pdf",
        "medians_main_pdf":       None,
        "medians_methodology_pdf": None,
        "medians_notice_pdf":     None,
        "medians_attachment_pdf": None,
        "state_il_pdf":           B.format(yy="12") + "/State_Incomelimits_Report.pdf",
        "income_limits_30_pdf":   None,
    },

    2013: {
        "section8":               B.format(yy="13") + "/Section8.xls",
        "section235_236":         B.format(yy="13") + "/S235_S236.xls",
        "npg":                    None,
        "national_nonmet":        None,
        "hud_sec8_pdf":           B.format(yy="13") + "/HUD_sec8_13.pdf",
        "hud_sec236_pdf":         B.format(yy="13") + "/Notice_235-236.pdf",
        "section8_pdf":           B.format(yy="13") + "/IncomeLimits_Section8.pdf",
        "section235_236_pdf":     B.format(yy="13") + "/IncomeLimits_Section236_BMIR.pdf",
        "section8_il_pdf":        None,
        "section235_236_il_pdf":  None,
        "area_definitions_pdf":   B.format(yy="13") + "/area_definitions.pdf",
        "briefing_material_pdf":  B.format(yy="13") + "/IncomeLimitsBriefingMaterial_FY13.pdf",
        "medians_pdf":            B.format(yy="13") + "/Medians2013.pdf",
        "medians_main_pdf":       None,
        "medians_methodology_pdf": None,
        "medians_notice_pdf":     None,
        "medians_attachment_pdf": None,
        "state_il_pdf":           B.format(yy="13") + "/State_Incomelimits_Report.pdf",
        "income_limits_30_pdf":   None,
    },

    2014: {
        # HUD did not publish Section 8 as Excel for FY2014 — PDF only
        "section8":               B.format(yy="14") + "/IncomeLimits_Section8_Rev.pdf",
        "section235_236":         B.format(yy="14") + "/S235_S236.xls",
        "npg":                    None,
        "national_nonmet":        None,
        "hud_sec8_pdf":           B.format(yy="14") + "/HUD_sec8_14.pdf",
        "hud_sec236_pdf":         B.format(yy="14") + "/HUD_sec236_14.pdf",
        "section8_pdf":           None,   # section8 key itself IS the PDF for 2014
        "section235_236_pdf":     B.format(yy="14") + "/IncomeLimits_Section236_BMIR.pdf",
        "section8_il_pdf":        None,
        "section235_236_il_pdf":  None,
        "area_definitions_pdf":   B.format(yy="14") + "/area_definitions.pdf",
        "briefing_material_pdf":  B.format(yy="14") + "/IncomeLimitsBriefingMaterial_FY14_Rev.pdf",
        "medians_pdf":            B.format(yy="14") + "/Medians2014_v2.pdf",
        "medians_main_pdf":       None,
        "medians_methodology_pdf": None,
        "medians_notice_pdf":     None,
        "medians_attachment_pdf": None,
        "state_il_pdf":           B.format(yy="14") + "/State_Incomelimits_Report.pdf",
        "income_limits_30_pdf":   None,
    },

    2015: {
        "section8":               B.format(yy="15") + "/Section8_Rev.xlsx",
        "section235_236":         B.format(yy="15") + "/S235_S236_Rev.xlsx",
        "npg":                    B.format(yy="15") + "/HUD_IL_NPG_Rev.xlsx",
        "national_nonmet":        None,
        "hud_sec8_pdf":           B.format(yy="15") + "/HUD_sec8_15.pdf",
        "hud_sec236_pdf":         B.format(yy="15") + "/HUD_sec236_15.pdf",
        "section8_pdf":           None,
        "section235_236_pdf":     None,
        "section8_il_pdf":        B.format(yy="15") + "/Section8_IncomeLimits_Rev.pdf",
        "section235_236_il_pdf":  B.format(yy="15") + "/IncomeLimits_Section236_BMIR_Rev.pdf",
        "area_definitions_pdf":   B.format(yy="15") + "/area_definitions.pdf",
        "briefing_material_pdf":  B.format(yy="15") + "/IncomeLimitsBriefingMaterial_FY15_Rev_2.pdf",
        "medians_pdf":            B.format(yy="15") + "/Medians2015.pdf",
        "medians_main_pdf":       None,
        "medians_methodology_pdf": None,
        "medians_notice_pdf":     None,
        "medians_attachment_pdf": None,
        "state_il_pdf":           B.format(yy="15") + "/State_Incomelimits_Report.pdf",
        "income_limits_30_pdf":   B.format(yy="15") + "/IncomeLimits_30_Rev.pdf",
    },

    2016: {
        "section8":               B.format(yy="16") + "/Section8-FY16.xlsx",
        "section235_236":         B.format(yy="16") + "/S235-S236-FY16.xlsx",
        "npg":                    B.format(yy="16") + "/HUD-IL-NPG-FY16.xlsx",
        "national_nonmet":        None,
        "hud_sec8_pdf":           B.format(yy="16") + "/HUD-sec8-FY16.pdf",
        "hud_sec236_pdf":         B.format(yy="16") + "/HUD-sec236-2016.pdf",
        "section8_pdf":           None,
        "section235_236_pdf":     None,
        "section8_il_pdf":        B.format(yy="16") + "/Section8-IncomeLimits-FY16.pdf",
        "section235_236_il_pdf":  B.format(yy="16") + "/IncomeLimits-Section236-BMIR-FY16.pdf",
        "area_definitions_pdf":   B.format(yy="16") + "/area-definitions-FY16.pdf",
        "briefing_material_pdf":  B.format(yy="16") + "/IncomeLimitsBriefingMaterial-FY16.pdf",
        "medians_pdf":            B.format(yy="16") + "/Medians2016.pdf",
        "medians_main_pdf":       None,
        "medians_methodology_pdf": None,
        "medians_notice_pdf":     None,
        "medians_attachment_pdf": None,
        "state_il_pdf":           B.format(yy="16") + "/State-Incomelimits-Report-FY16.pdf",
        "income_limits_30_pdf":   B.format(yy="16") + "/IncomeLimits-30-FY16.pdf",
    },

    2017: {
        "section8":               B.format(yy="17") + "/Section8-FY17.xlsx",
        "section235_236":         B.format(yy="17") + "/S235-S236-FY17.xlsx",
        "npg":                    B.format(yy="17") + "/HUD-IL-NPG-FY17.xlsx",
        "national_nonmet":        None,
        "hud_sec8_pdf":           B.format(yy="17") + "/HUD-sec8-FY17.pdf",
        "hud_sec236_pdf":         B.format(yy="17") + "/HUD-sec236-2017.pdf",
        "section8_pdf":           None,
        "section235_236_pdf":     None,
        "section8_il_pdf":        B.format(yy="17") + "/Section8-IncomeLimits-FY17.pdf",
        "section235_236_il_pdf":  B.format(yy="17") + "/IncomeLimits-Section236-BMIR-FY17.pdf",
        "area_definitions_pdf":   B.format(yy="17") + "/area-definitions-FY17.pdf",
        "briefing_material_pdf":  B.format(yy="17") + "/IncomeLimitsBriefingMaterial-FY17.pdf",
        "medians_pdf":            B.format(yy="17") + "/Medians2017.pdf",
        "medians_main_pdf":       None,
        "medians_methodology_pdf": None,
        "medians_notice_pdf":     None,
        "medians_attachment_pdf": None,
        "state_il_pdf":           B.format(yy="17") + "/State-Incomelimits-Report-FY17.pdf",
        "income_limits_30_pdf":   B.format(yy="17") + "/IncomeLimits-30-FY17.pdf",
    },

    2018: {
        "section8":               B.format(yy="18") + "/Section8-FY18.xlsx",
        "section235_236":         B.format(yy="18") + "/S235-S236-FY18.xlsx",
        "npg":                    B.format(yy="18") + "/HUD-IL-NPG-FY18.xlsx",
        "national_nonmet":        None,
        "hud_sec8_pdf":           B.format(yy="18") + "/HUD-sec8-FY18r.pdf",
        "hud_sec236_pdf":         B.format(yy="18") + "/HUD-sec236-2018.pdf",
        "section8_pdf":           None,
        "section235_236_pdf":     None,
        "section8_il_pdf":        B.format(yy="18") + "/Section8-IncomeLimits-FY18.pdf",
        "section235_236_il_pdf":  B.format(yy="18") + "/IncomeLimits-Section236-BMIR-FY18.pdf",
        "area_definitions_pdf":   B.format(yy="18") + "/area-definitions-FY18.pdf",
        "briefing_material_pdf":  B.format(yy="18") + "/IncomeLimitsMethodology-FY18.pdf",
        "medians_pdf":            B.format(yy="18") + "/Medians2018r.pdf",
        "medians_main_pdf":       None,
        "medians_methodology_pdf": B.format(yy="18") + "/Medians-Methodology-FY18r.pdf",
        "medians_notice_pdf":     None,
        "medians_attachment_pdf": None,
        "state_il_pdf":           B.format(yy="18") + "/State-Incomelimits-Report-FY18r.pdf",
        "income_limits_30_pdf":   B.format(yy="18") + "/IncomeLimits-30-FY18.pdf",
    },

    2019: {
        "section8":               B.format(yy="19") + "/Section8-FY19.xlsx",
        "section235_236":         B.format(yy="19") + "/S235-S236-FY19.xlsx",
        "npg":                    B.format(yy="19") + "/HUD-IL-NPG-FY19.xlsx",
        "national_nonmet":        None,
        "hud_sec8_pdf":           B.format(yy="19") + "/HUD-sec8-FY19r.pdf",
        "hud_sec236_pdf":         B.format(yy="19") + "/HUD-sec236-2019.pdf",
        "section8_pdf":           None,
        "section235_236_pdf":     None,
        "section8_il_pdf":        B.format(yy="19") + "/Section8-IncomeLimits-FY19.pdf",
        "section235_236_il_pdf":  B.format(yy="19") + "/IncomeLimits-Section236-BMIR-FY19.pdf",
        "area_definitions_pdf":   B.format(yy="19") + "/area-definitions-FY19.pdf",
        "briefing_material_pdf":  B.format(yy="19") + "/IncomeLimitsMethodology-FY19.pdf",
        "medians_pdf":            B.format(yy="19") + "/Medians2019r.pdf",
        "medians_main_pdf":       None,
        "medians_methodology_pdf": B.format(yy="19") + "/Medians-Methodology-FY19r.pdf",
        "medians_notice_pdf":     None,
        "medians_attachment_pdf": None,
        "state_il_pdf":           B.format(yy="19") + "/State-Incomelimits-Report-FY19r.pdf",
        "income_limits_30_pdf":   B.format(yy="19") + "/IncomeLimits-30-FY19.pdf",
    },

    2020: {
        "section8":               B.format(yy="20") + "/Section8-FY20.xlsx",
        "section235_236":         B.format(yy="20") + "/S235-S236-FY20.xlsx",
        "npg":                    B.format(yy="20") + "/HUD-IL-NPG-FY20.xlsx",
        "national_nonmet":        None,
        "hud_sec8_pdf":           B.format(yy="20") + "/HUD-sec8-FY20r.pdf",
        "hud_sec236_pdf":         B.format(yy="20") + "/HUD-sec236-2020.pdf",
        "section8_pdf":           None,
        "section235_236_pdf":     None,
        "section8_il_pdf":        B.format(yy="20") + "/Section8-IncomeLimits-FY20.pdf",
        "section235_236_il_pdf":  B.format(yy="20") + "/IncomeLimits-Section236-BMIR-FY20.pdf",
        "area_definitions_pdf":   B.format(yy="20") + "/area-definitions-FY20.pdf",
        "briefing_material_pdf":  B.format(yy="20") + "/IncomeLimitsMethodology-FY20.pdf",
        "medians_pdf":            B.format(yy="20") + "/Medians2020r.pdf",
        "medians_main_pdf":       None,
        "medians_methodology_pdf": B.format(yy="20") + "/Medians-Methodology-FY20r.pdf",
        "medians_notice_pdf":     None,
        "medians_attachment_pdf": None,
        "state_il_pdf":           B.format(yy="20") + "/State-Incomelimits-Report-FY20r.pdf",
        "income_limits_30_pdf":   B.format(yy="20") + "/IncomeLimits-30-FY20.pdf",
    },

    2021: {
        "section8":               B.format(yy="21") + "/Section8-FY21.xlsx",
        "section235_236":         B.format(yy="21") + "/S235-S236-FY21.xlsx",
        "npg":                    B.format(yy="21") + "/HUD-IL-NPG-FY21.xlsx",
        "national_nonmet":        None,
        "hud_sec8_pdf":           B.format(yy="21") + "/HUD-sec8-FY21.pdf",
        "hud_sec236_pdf":         B.format(yy="21") + "/HUD-sec236-2021.pdf",
        "section8_pdf":           None,
        "section235_236_pdf":     None,
        "section8_il_pdf":        B.format(yy="21") + "/Section8-IncomeLimits-FY21.pdf",
        "section235_236_il_pdf":  B.format(yy="21") + "/IncomeLimits-Section236-BMIR-FY21.pdf",
        "area_definitions_pdf":   B.format(yy="21") + "/area-definitions-FY21.pdf",
        "briefing_material_pdf":  B.format(yy="21") + "/IncomeLimitsMethodology-FY21.pdf",
        "medians_pdf":            B.format(yy="21") + "/Medians2021.pdf",
        "medians_main_pdf":       None,
        "medians_methodology_pdf": B.format(yy="21") + "/Medians-Methodology-FY21.pdf",
        "medians_notice_pdf":     None,
        "medians_attachment_pdf": None,
        "state_il_pdf":           B.format(yy="21") + "/State-Incomelimits-Report-FY21.pdf",
        "income_limits_30_pdf":   B.format(yy="21") + "/IncomeLimits-30-FY21.pdf",
    },

    2022: {
        "section8":               B.format(yy="22") + "/Section8-FY22.xlsx",
        "section235_236":         B.format(yy="22") + "/S235-S236-FY22.xlsx",
        "npg":                    B.format(yy="22") + "/HUD-IL-NPG-FY22.xlsx",
        "national_nonmet":        None,
        "hud_sec8_pdf":           B.format(yy="22") + "/HUD-sec8-FY22.pdf",
        "hud_sec236_pdf":         B.format(yy="22") + "/HUD-sec236-FY22.pdf",
        "section8_pdf":           None,
        "section235_236_pdf":     None,
        "section8_il_pdf":        B.format(yy="22") + "/Section8-IncomeLimits-FY22.pdf",
        "section235_236_il_pdf":  B.format(yy="22") + "/IncomeLimits-Section236-BMIR-FY22.pdf",
        "area_definitions_pdf":   B.format(yy="22") + "/area-definitions-FY22.pdf",
        "briefing_material_pdf":  B.format(yy="22") + "/IncomeLimitsMethodology-FY22.pdf",
        "medians_pdf":            None,   # no individual Medians{YYYY}.pdf for 2022
        "medians_main_pdf":       None,
        "medians_methodology_pdf": B.format(yy="22") + "/Medians-Methodology-FY22.pdf",
        "medians_notice_pdf":     B.format(yy="22") + "/Medians-FY22-Notice.pdf",
        "medians_attachment_pdf": None,
        "state_il_pdf":           B.format(yy="22") + "/State-Incomelimits-Report-FY22.pdf",
        "income_limits_30_pdf":   B.format(yy="22") + "/IncomeLimits-30-FY22.pdf",
    },

    2023: {
        "section8":               B.format(yy="23") + "/Section8-FY23.xlsx",
        "section235_236":         B.format(yy="23") + "/S235-S236-FY23.xlsx",
        "npg":                    B.format(yy="23") + "/HUD-IL-NPG-FY23.xlsx",
        "national_nonmet":        B.format(yy="23") + "/FY2023-National-Non-Met-Very-Low-Income-Limits.xlsx",
        "hud_sec8_pdf":           B.format(yy="23") + "/HUD-sec8-FY23.pdf",
        "hud_sec236_pdf":         B.format(yy="23") + "/HUD-sec236-FY23.pdf",
        "section8_pdf":           None,
        "section235_236_pdf":     None,
        "section8_il_pdf":        B.format(yy="23") + "/Section8-IncomeLimits-FY23.pdf",
        "section235_236_il_pdf":  B.format(yy="23") + "/IncomeLimits-Section236-BMIR-FY23.pdf",
        "area_definitions_pdf":   B.format(yy="23") + "/area-definitions-FY23.pdf",
        "briefing_material_pdf":  B.format(yy="23") + "/IncomeLimitsMethodology-FY23.pdf",
        "medians_pdf":            None,
        "medians_main_pdf":       None,
        "medians_methodology_pdf": B.format(yy="23") + "/Medians-Methodology-FY23.pdf",
        "medians_notice_pdf":     B.format(yy="23") + "/Medians-FY23-Notice.pdf",
        "medians_attachment_pdf": B.format(yy="23") + "/FY23-Median-Attachment-State-Medians.pdf",
        "state_il_pdf":           B.format(yy="23") + "/State-Incomelimits-Report-FY23.pdf",
        "income_limits_30_pdf":   B.format(yy="23") + "/IncomeLimits-30-FY23.pdf",
    },

    2024: {
        "section8":               B.format(yy="24") + "/Section8-FY24.xlsx",
        "section235_236":         B.format(yy="24") + "/S235-S236-FY24.xlsx",
        "npg":                    B.format(yy="24") + "/HUD-IL-NPG-FY24.xlsx",
        "national_nonmet":        B.format(yy="24") + "/FY2024-National-Non-Met-Very-Low-Income-Limits.xlsx",
        "hud_sec8_pdf":           B.format(yy="24") + "/HUD-sec8-FY24.pdf",
        "hud_sec236_pdf":         B.format(yy="24") + "/HUD-sec236-FY24.pdf",
        "section8_pdf":           None,
        "section235_236_pdf":     None,
        "section8_il_pdf":        B.format(yy="24") + "/Section8-IncomeLimits-FY24.pdf",
        "section235_236_il_pdf":  B.format(yy="24") + "/IncomeLimits-Section236-BMIR-FY24.pdf",
        "area_definitions_pdf":   B.format(yy="24") + "/area-definitions-FY24.pdf",
        "briefing_material_pdf":  B.format(yy="24") + "/IncomeLimitsMethodology-FY24.pdf",
        "medians_pdf":            None,
        "medians_main_pdf":       None,
        "medians_methodology_pdf": B.format(yy="24") + "/Medians-Methodology-FY24.pdf",
        "medians_notice_pdf":     B.format(yy="24") + "/Medians-FY24-Notice.pdf",
        "medians_attachment_pdf": B.format(yy="24") + "/FY24-Median-Attachment-State-Medians.pdf",
        "state_il_pdf":           B.format(yy="24") + "/State-Incomelimits-Report-FY24.pdf",
        "income_limits_30_pdf":   B.format(yy="24") + "/IncomeLimits-30-FY24.pdf",
    },

    2025: {
        "section8":               B.format(yy="25") + "/Section8-FY25.xlsx",
        "section235_236":         B.format(yy="25") + "/S235-S236-FY25.xlsx",
        "npg":                    B.format(yy="25") + "/HUD-IL-NPG-FY25.xlsx",
        "national_nonmet":        B.format(yy="25") + "/FY2025-National-Non-Met-Very-Low-Income-Limits.xlsx",
        "hud_sec8_pdf":           B.format(yy="25") + "/HUD-sec8-FY25.pdf",
        "hud_sec236_pdf":         B.format(yy="25") + "/HUD-sec236-FY25.pdf",
        "section8_pdf":           None,
        "section235_236_pdf":     None,
        "section8_il_pdf":        B.format(yy="25") + "/Section8-IncomeLimits-FY25.pdf",
        "section235_236_il_pdf":  B.format(yy="25") + "/IncomeLimits-Section236-BMIR-FY25.pdf",
        "area_definitions_pdf":   B.format(yy="25") + "/area-definitions-FY25.pdf",
        "briefing_material_pdf":  B.format(yy="25") + "/IncomeLimitsMethodology-FY25.pdf",
        "medians_pdf":            None,
        "medians_main_pdf":       None,
        "medians_methodology_pdf": B.format(yy="25") + "/Medians-Methodology-FY25.pdf",
        "medians_notice_pdf":     B.format(yy="25") + "/Medians-FY25-Notice.pdf",
        "medians_attachment_pdf": B.format(yy="25") + "/FY25-Median-Attachment-State-Medians.pdf",
        "state_il_pdf":           B.format(yy="25") + "/State-Incomelimits-Report-FY25.pdf",
        "income_limits_30_pdf":   B.format(yy="25") + "/IncomeLimits-30-FY25.pdf",
    },
}

# All known dataset keys (used to emit NO_DATA for years not in _YEAR_URLS)
_ALL_KEYS = [
    "section8", "section235_236", "npg", "national_nonmet",
    "hud_sec8_pdf", "hud_sec236_pdf",
    "section8_pdf", "section235_236_pdf",
    "section8_il_pdf", "section235_236_il_pdf",
    "area_definitions_pdf", "briefing_material_pdf",
    "medians_pdf", "medians_main_pdf", "medians_methodology_pdf",
    "medians_notice_pdf", "medians_attachment_pdf",
    "state_il_pdf", "income_limits_30_pdf",
]


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.huduser.gov/portal/datasets/il.html",
    })
    try:
        session.get("https://www.huduser.gov/portal/datasets/il.html", timeout=30)
    except Exception:
        pass
    return session


def ingest_year(session: requests.Session, year: int) -> None:
    if year not in _YEAR_URLS:
        print(f"\n[Year {year}] — no URL map defined, skipping")
        return

    download_dir = _ROOT / "bronze_files" / "ami" / str(year)
    download_dir.mkdir(parents=True, exist_ok=True)

    url_map = _YEAR_URLS[year]

    print(f"\n[Year {year}]")
    for dataset_name in _ALL_KEYS:
        url = url_map.get(dataset_name)
        if url is None:
            print(f"  [SKIP] {dataset_name}: not published by HUD for {year}")
            _logbook.write("AMI", dataset_name, year, "NO_DATA",
                           reason=f"not published by HUD for {year}")
            continue

        ext = Path(url).suffix  # .xls, .xlsx, or .pdf
        filename = f"{dataset_name}_{year}{ext}"
        dest = download_dir / filename

        if dest.exists():
            print(f"  [SKIP] {dataset_name}: already downloaded ({dest.name})")
            _logbook.write("AMI", dataset_name, year, "SKIP", dest.name,
                           url=url, reason="already exists")
            continue

        try:
            r = session.get(url, timeout=120)
            r.raise_for_status()
            if len(r.content) == 0:
                print(f"  [EMPTY] {dataset_name}: 0 bytes from {url}")
                _logbook.write("AMI", dataset_name, year, "SKIP", filename, url=url,
                               reason="server returned 0 bytes")
                continue

            dest.write_bytes(r.content)
            print(f"  [OK] {dataset_name} -> {filename} ({len(r.content):,} bytes)")
            _logbook.write("AMI", dataset_name, year, "OK", filename, len(r.content), url)

        except requests.HTTPError as e:
            print(f"  [FAIL] {dataset_name}: HTTP {e.response.status_code} — {url}")
            _logbook.write("AMI", dataset_name, year, "FAIL", filename, url=url, reason=str(e))
        except Exception as e:
            print(f"  [FAIL] {dataset_name}: {e}")
            _logbook.write("AMI", dataset_name, year, "FAIL", filename, url=url, reason=str(e))


def _parse_years(raw: list[str]) -> list[int]:
    """Accept individual years and/or ranges like 2010-2025."""
    years: list[int] = []
    for token in raw:
        if "-" in token and not token.lstrip("-").isdigit():
            parts = token.split("-")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                start, end = int(parts[0]), int(parts[1])
                years.extend(range(start, end + 1))
            else:
                raise argparse.ArgumentTypeError(f"Invalid range: {token!r}")
        else:
            years.append(int(token))
    return sorted(set(years))


def main() -> None:
    parser = argparse.ArgumentParser(description="Download all HUD AMI files (Excel + PDF).")
    parser.add_argument(
        "--year", nargs="+", required=True, metavar="YEAR",
        help="Years to download: individual (2024 2025) or range (2010-2025) or mixed",
    )
    args = parser.parse_args()
    years = _parse_years(args.year)

    session = make_session()
    for year in years:
        ingest_year(session, year)

    print("\nDone.")


if __name__ == "__main__":
    main()
