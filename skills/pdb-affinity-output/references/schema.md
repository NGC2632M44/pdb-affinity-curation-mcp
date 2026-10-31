# Curation schema

Use one row per measurement or explicit no-affinity observation.

## Identity and source

| Column | Meaning |
|---|---|
| `PDB_ID` | Four-character accession, uppercase |
| `DOI` | Paper that justifies the PDB-to-record association |
| `Evidence_DOI` | DOI of the actual measurement or primary evidence |
| `Source_File` | Main paper, supplement or database file |
| `Source_Location` | Table, figure, page or supplementary section |
| `Paper_Compound_Label` | Label used by the source paper |
| `Ligand_InChIKey` | Exact chemical identity used for cross-source joins |
| `Ligand_CAS` | CAS registry number for Reaxys/SciFinder joins |
| `Identity_Evidence` | HET, chemical name, exact cross-reference or stated limitation |

## Measurement

| Column | Meaning |
|---|---|
| `Assay_Type` | Kd, Ki, IC50, EC50, Tm, metal-ion Kd or other |
| `Endpoint_As_Defined_By_Paper` | Preserve the paper's exact definition |
| `Value_Original` | Printed value, including bound or qualifier |
| `Unit_Original` | Printed unit |
| `Uncertainty_Original` | SD, SE, CI, range, n or model note |
| `Value_nM` | Numeric nM only when a direct conversion is valid |
| `Conversion_Applied` | Explicit unit conversion |

## Biological context

| Column | Meaning |
|---|---|
| `Target_Enzyme` | Protein or polymerase complex |
| `Virus_or_Strain` | Virus and strain |
| `Mutation_Profile_per_paper` | WT, I38T, D118A or other |
| `Assay_System` | Cell-free, cell culture, structural or functional |
| `Cell_Line` | MDCK, 293T or cell-free |
| `Method` | FRET, FP, ITC, MST, DSF, AlphaScreen, cryo-EM, etc. |

## Judgment and provenance

| Column | Meaning |
|---|---|
| `Why_No_Value` | Required when no quantitative value is stored |
| `Basis` | Evidence supporting the row |
| `Confidence` | high, medium or low |
| `Confidence_Reason` | Concrete reason, not a generic word |
| `Evidence_Quote` | Short quote or direct locator |
| `Provenance_Class` | current, cited, model, prior review or unresolved |
| `Affinity_Status` | Controlled vocabulary from `SKILL.md` |
| `Affinity_Applies_To_PDB` | Exact compound match, context only, phenotype or none |
| `Correction_ID` | Stable row identifier in the corrected dataset |
| Source_Type | paper_main, paper_si, patent, RCSB, Reaxys, SciFinder, ChEMBL, BindingDB |
| Source_Status | available, blocked, missing, hashed |
| Artifact_SHA256 | Hash of downloaded source file |
| Supplement_File | Local SI file used for A-tier evidence |
| Supplement_SHA256 | Hash of the SI file |
| Artifact_Type | native_pdf, scanned_pdf, image, docx, xml, html, patent |
| Visual_Review_Required | Whether figures/tables/images need rendered-page review |
| Visual_Review_Status | yes, no, blocked |
| OCR_Quality | high, medium, low, not_applicable |
| Locator | Page, table, figure, patent paragraph or database record |
| Evidence_Relation | current, cited, prior_series, model, secondary |
| Entity_Role | inhibitor_candidate, substrate, metal, RNA, additive, buffer, unknown |
| Measurement_Kind | binding, functional, cell, cytotoxicity, Tm, concentration, other |
| Mutation_Evidence_Class | paper_and_sequence, paper_only, sequence_inference_only, not_applicable |
| Construct_Scope | exact_crystal_construct, construct_context, cell_context, unknown |
| Mapping_Evidence | exact_inchikey, het_and_paper, series_table, name_only, unresolved |
| Evidence_Tier | A_primary_fulltext_si, B_primary_fulltext, C_patent_plus_paper, D_secondary_only, U_unresolved |
| Read_Level | fulltext_si, fulltext, abstract, metadata_only, none |
| Evidence_Status | accepted, needs_fulltext, needs_primary_source, conflict, rejected, blocked |
| Patent_ID | Patent family or publication number |
| Database_Record_ID | Reaxys/SciFinder/ChEMBL/BindingDB identifier |
| Conflict_Status | none, title_het_conflict, accession_conflict, genotype_conflict, value_conflict |
| Review_Checklist | Short evidence checklist showing what was read |

Use blank cells for genuinely absent values. Do not write `N/A` in a numeric
column.

## Evidence-grade rule

`accepted` direct affinity requires A/B/C evidence, a primary text locator and
an exact or explicitly mapped compound identity. `D_secondary_only` is a lead
and cannot satisfy acceptance.

The evidence pack's `review_checklist.csv` is part of strict validation. For
accepted rows it must mark the main text, methods, tables/figures, reference
chain, patent search, visual review, and at least one of Reaxys/SciFinder as
checked. An A-tier row additionally requires the supplement to be marked read.

## Blocker ledger

`blocker_resolution.csv` has one row per raw blocker:

| Column | Meaning |
|---|---|
| `PDB_ID` | Entry affected, or `all` for a batch-wide access condition |
| `Blocker_Type` | Controlled failure type |
| `Original_Detail` | Original failure from `blockers.csv` |
| `Resolution_Status` | resolved, resolved_with_context, evidence_exhausted_no_hit, requires_authorized_session, open |
| `Resolution_Route` | API, OA, browser, exact-component peer, patent, licensed database |
| `Resolution_Evidence` | Path, DOI, patent paragraph or identity receipt |
| `Evidence_SHA256` | Hash when the receipt is a file |
| `Required_For_Acceptance` | yes or no |
| `Remaining_Action` | Required next action when not closed |

Accepted rows cannot depend on a `yes` blocker left `open` or
`requires_authorized_session`.

## Human comparison

`human_comparison.csv` is not part of the curated scientific schema. It compares
the independent rows with the human draft and classifies each row as match,
evidence-corrected, evidence-added, missing or unresolved disagreement. Keep it
with `human_comparison_metrics.json` and the correction log.
