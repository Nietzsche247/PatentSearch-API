# Fixture sources

These are the fixtures of claimgraph, PatentRef's claim parser. claimgraph is a private package and is not part of this
repository; the fork keeps the claim sets here because its structure tests read them (lapse_accounts/tests/
structure_files.py and test_structure_claims.py). The note below is claimgraph's own; the tools it names stay with
claimgraph.

37 real US patents, stored one per file as `US{patent_id}.tsv` in the PatentsView
`g_claims` column layout:

    patent_id  claim_sequence  claim_text  dependent  claim_number  exemplary

`manifest.json` lists era, kind, source, claim count and whether the `dependent`
column comes from the source (`patentsview`, `xml_claim_ref`) or is absent (`none`).
`tools/build_fixtures.py` rebuilds every file from the sources below.

No text here was typed, summarized or paraphrased. Every claim was copied by program
from a bulk dataset. WebFetch was tried first on patents.google.com and refused to
quote more than 125 characters verbatim, so it was not used for any fixture.

## Sources

### 1. AIPD claims in PatentsView g_claims layout (23 patents, 1976 to 2019)

* Dataset: https://huggingface.co/datasets/anonymousdemo/AIPD_nlp_g_all_claims
  (file `AIPD_nlp_g_all_claims.csv`, 308 MB, 601,790 claims of 30,752 patents).
* What it is: the USPTO Artificial Intelligence Patent Dataset joined to the
  PatentsView `g_claims` tables. Columns: appl_id, flag_patent, doc_id,
  claim_sequence, claim_text, dependent, claim_number.
* Copied as is: doc_id becomes patent_id; claim_sequence, claim_text, dependent and
  claim_number are byte for byte from the CSV; exemplary is empty (not in the source).
* This source shows the real `dependent` formats: `claim N`, `claims N, M, ...` and an
  empty string for independent claims. Ranges are stored as endpoints only
  (`any one of claims 10 to 15` has the field `claims 10, 15`). claim_number is `1`
  in older years and zero padded `00001` in later ones.
* Licence: not stated on the dataset card. The claim text is a US government work;
  PatentsView data is published under CC BY 4.0.

### 2. PatentPulse (13 patents, granted 2024 and 2025)

* Dataset: https://huggingface.co/datasets/theworker02/patentpulse (parquet built
  from the official USPTO weekly grant XML releases, snapshot 2026-08-28).
* Read with HTTP range requests (only the needed row groups):
  `data/train/part-00000.parquet` (2024 grants) and
  `data/validation/part-00000.parquet` (2025 grants). Row group and row index for
  each patent are in `tools/build_fixtures.py`; the source weekly archive name
  (for example `ipg251230_r2.zip`) is kept in `manifest.json`.
* Transformations: the whole-document `claims_text` was split at claim number
  boundaries, accepting a boundary only when its number is the next expected claim
  number; runs of ASCII whitespace were collapsed to one space (the PatentsView
  claim_text convention, see the cross check below); patent ids were written the
  PatentsView way (`PP036316` to `PP36316`, `RE050239` to `RE50239`).
* `dependent` is empty for these rows because the source has no such field.
  The manifest marks them `dependent_field: none` and the tests parse them with the
  field ignored.
* Two different raw spacings survive from upstream and are left alone: the 2024
  shard writes `1. A`, the 2025 shard writes `1 . A`.
* Licence: dataset marked `other`; the records are USPTO public bulk data.

### 3. USPTO grant XML for a 2024 design patent (1 patent)

* Dataset: https://huggingface.co/datasets/SoichiOnozuka/design-patents-not-in-impact
  (CC BY-SA 4.0), archive `images/2024.tar.zst`. Only the first 4 MB were fetched and
  decompressed; `USD1009402-20240102.XML` was taken from that prefix.
* claim_text is the text of `<claim>`; `dependent` would come from `<claim-ref>`
  tags (there are none in a design claim).

## Verification

* Cross check between sources 1 and 2: US10073891 (9 claims, 6,232 characters) and
  US10198363 (24 claims, 17,956 characters) appear in both AIPD and PatentPulse.
  After whitespace collapsing the joined claim text is identical character for
  character. This also confirms that PatentsView claim_text is the USPTO XML text
  with whitespace collapsed (including the ` , ` spacing left where `<claim-ref>`
  tags were removed).
* Sources that were tried and refused: Google BigQuery `patents-public-data` (the
  `bq` CLI is not authenticated); patents.google.com by curl (HTTP 503 automated
  query page); data.patentsview.org and bulkdata.uspto.gov (refused by the egress
  proxy); the PatentsView S3 bucket (HTTP 403); api.uspto.gov (needs an API key).
  HUPD was reachable but holds applications (2004 to 2018), not granted claims.

## Patents

| patent_id | era | kind | source | note |
|---|---|---|---|---|
| 3995389 | 1976-1979 | apparatus | AIPD | lettered elements `a.` `b.`, "consisting of" |
| 3996569 | 1976-1979 | apparatus | AIPD | "characterized" |
| 4007548 | 1976-1979 | method | AIPD | field `claims 5, 1` includes a body reference |
| 4021937 | 1976-1979 | apparatus | AIPD | "comprising in combination" |
| 4156107 | 1976-1979 | method/apparatus | AIPD | `claim 2 or 3`, "characterized by the steps of" |
| 4359730 | 1980s | apparatus | AIPD | multiple dependency with "or" |
| RE32012 | 1980s | reissue | AIPD | Jepson style dependent claims |
| 5402504 | 1990s | apparatus | AIPD | multiple dependency |
| 5404514 | 1990s | method | AIPD | "consisting" |
| 5414838 | 1990s | crm | AIPD | storage medium |
| 5418943 | 1990s | system | AIPD | `any of claims 2, 4, 6, or 8` |
| 6023673 | 2000s | apparatus | AIPD | "characterized" |
| 6023701 | 2000s | crm | AIPD | storage medium claim |
| 7689559 | 2010s | method | AIPD | `any one of claims 1 - 3 or 4 - 11` |
| 7705229 | 2010s | method | AIPD | `(a)` to `(g)` steps |
| 7844447 | 2010s | method | AIPD | body references |
| 7852411 | 2010s | apparatus | AIPD | "Apparatus according to claim 1" |
| 8032377 | 2010s | method | AIPD | `claims 1 , 2 , and 3 to 6` |
| 10034028 | 2010s | method | AIPD | independent claims citing others |
| 10037710 | 2010s | method | AIPD | "characterized in that" |
| 10049105 | 2010s | apparatus | AIPD | claim 2 incorporates claim 1 in its body |
| 10073891 | 2010s | system | AIPD | comma joined `a.` to `h.` steps; cross checked |
| RE42441 | 2010s | reissue | AIPD | added claims with empty field; markup merged |
| 12180353 | 2024-2025 | composition | PatentPulse | wt % ranges, structure image placeholder |
| 12175013 | 2024-2025 | system | PatentPulse | nested "configured to" lists |
| 12167790 | 2024-2025 | apparatus | PatentPulse | workstation module |
| PP36316 | 2024-2025 | plant | PatentPulse | rose variety |
| RE50239 | 2024-2025 | reissue | PatentPulse | markup merged |
| D1107373 | 2024-2025 | design | PatentPulse | design claim without number prefix |
| PP37179 | 2024-2025 | plant | PatentPulse | Mentha variety |
| RE50714 | 2024-2025 | reissue | PatentPulse | markup merged ("level level of warnings") |
| 12511007 | 2024-2025 | crm | PatentPulse | method, device, storage medium |
| 12505332 | 2024-2025 | method | PatentPulse | deep learning method, system, program |
| 12496280 | 2024-2025 | composition | PatentPulse | "characterized in that", Markush list |
| 12486226 | 2024-2025 | composition | PatentPulse | "A compound of formula" with the structure image removed |
| 12474417 | 2024-2025 | method | PatentPulse | capacitor lifetime method |
| D1009402 | 2024-2025 | design | USPTO XML | design claim |

Gap: no chemical composition claims from the 1970s to 1990s. AIPD covers AI related
patents only and the open 2018 to 2026 sources do not reach back that far.
