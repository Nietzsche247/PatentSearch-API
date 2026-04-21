# Endpoint Dictionary

Below is a list of publically available endpoints along with a list of fields corresponding to each endpoint. Each of
these endpoints can be queried using the [API Query Language](/docs/docs/Search%20API/SearchAPIReference#api-query-language). The fields available for each endpoint can also be found on the [Swagger Page](https://search.patentsview.org/swagger-ui/) under the corresponding endpoint and schema dropdown items.

:::tip
Some endpoints support the ability to use the GET method to get resources by ID i.e. instead of supplying field query,
users can lookup a resource by ID. The
individual endpoints that support this will have an additional GET specification with the id required show in `{}`.
:::


## Granted Patent Endpoints

### Patent

<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/patent/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;patents</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>patent_id</td>
        <td>Patent number.</td>
        <td>string</td>
      </tr><tr>
        <td>patent_title</td>
        <td>Title of patent.</td>
        <td>text</td>
      </tr><tr>
        <td>patent_date</td>
        <td>Date when patent was granted.</td>
        <td>date</td>
      </tr><tr>
        <td>patent_year</td>
        <td>Year when patent was granted.</td>
        <td>integer</td>
      </tr><tr>
        <td>patent_type</td>
        <td>Category of patent. e.g. "utility", "design", etc.</td>
        <td>string</td>
      </tr><tr>
        <td>withdrawn</td>
        <td>Whether a patent has been withdrawn or not.</td>
        <td>boolean</td>
      </tr><tr>
        <td>patent_abstract</td>
        <td>The abstract text of the patent.</td>
        <td>text</td>
      </tr><tr>
        <td>wipo_kind</td>
        <td>WIPO document kind codes (http://www.uspto.gov/learning-and-resources/support-centers/electronic-business-center/kind-codes-included-uspto-patent).</td>
        <td>string</td>
      </tr><tr>
        <td>gov_interest_statement</td>
        <td>Text of declaration of government interest.</td>
        <td>text</td>
      </tr><tr>
        <td>patent_detail_desc_length</td>
        <td>The Length of the description text in characters.</td>
        <td>integer</td>
      </tr><tr>
        <td>patent_earliest_application_date</td>
        <td>The earliest filing date among all applications from which the patent claims priority.</td>
        <td>date</td>
      </tr><tr>
        <td>patent_num_us_patents_cited</td>
        <td>Number of U.S. patents referenced (cited) by this patent.</td>
        <td>integer</td>
      </tr><tr>
        <td>patent_num_us_applications_cited</td>
        <td>Number of U.S. patent applications referenced (cited) by this patent.</td>
        <td>integer</td>
      </tr><tr>
        <td>patent_num_foreign_documents_cited</td>
        <td>Count of foreign patent documents cited by this patent.</td>
        <td>integer</td>
      </tr><tr>
        <td>patent_num_total_documents_cited</td>
        <td>Total number of documents cited by the patent, including U.S. and foreign patent references and possibly non-patent literature.</td>
        <td>integer</td>
      </tr><tr>
        <td>patent_num_times_cited_by_us_patents</td>
        <td>Number of times subsequent U.S. patents have cited this patent.</td>
        <td>integer</td>
      </tr><tr>
        <td>patent_processing_days</td>
        <td>Total days of prosecution from earliest application filing date to the official date of grant.</td>
        <td>integer</td>
      </tr><tr>
        <td>patent_term_extension</td>
        <td>An extension to the grant term period in days.</td>
        <td>integer</td>
      </tr><tr>
        <td>patent_cpc_current_group_average_patent_processing_days</td>
        <td>Average number of days (from earliest application filing to grant) for patents that share the same current CPC group.</td>
        <td>integer</td>
      </tr><tr>
        <td>patent_uspc_current_mainclass_average_patent_processing_days</td>
        <td>Average number of days (from earliest application filing to grant) for patents that share the same current USPC main class.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
<h4>Nested Fields</h4>
  <div>
<details>
  <summary>applicants</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>applicant_name_first</td>
        <td>First name, if applicant is an individual.</td>
        <td>text</td>
      </tr><tr>
        <td>applicant_name_last</td>
        <td>Last name, if applicant is an individual.</td>
        <td>text</td>
      </tr><tr>
        <td>applicant_organization</td>
        <td>Organization name if applicant is an organization.</td>
        <td>text</td>
      </tr><tr>
        <td>applicant_sequence</td>
        <td>Order of the applicants on the patent application, beginning with zero; values for this field begin at 1.</td>
        <td>integer</td>
      </tr><tr>
        <td>applicant_type</td>
        <td>Type of applicant (applicant or applicant-inventor).</td>
        <td>string</td>
      </tr><tr>
        <td>location_id</td>
        <td>Unique location ID.</td>
        <td>string</td>
      </tr><tr>
        <td>applicant_designation</td>
        <td>Designation of the applicant.</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>application</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>application_id</td>
        <td>Application id assigned by USPTO.</td>
        <td>string</td>
      </tr><tr>
        <td>application_type</td>
        <td>Type or category of the application (e.g., utility, design).</td>
        <td>string</td>
      </tr><tr>
        <td>filing_date</td>
        <td>Date the patent application was filed with the USPTO. ISO format: YYYY-MM-DD.</td>
        <td>date</td>
      </tr><tr>
        <td>filing_type</td>
        <td>Patent application series code: 01-17 = utility application; 29 = design application; 35 = international design applications; 60-62 = provisional application; 90 = ex parte reexamination request; 95 = inter partes reexamination request; 96 = supplemental examination</td>
        <td>string</td>
      </tr><tr>
        <td>rule_47_flag</td>
        <td>Flag for inventor who was unable to be contacted at filing of patent.</td>
        <td>boolean</td>
      </tr><tr>
        <td>series_code</td>
        <td>Code representing a group of application serial numbers; "D" for some designs; (http://www.uspto.gov/web/offices/ac/ido/oeip/taf/filingyr.htm).</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>assignees</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>assignee</td>
        <td>An API request URL for additional details of this assignee.</td>
        <td>string</td>
      </tr><tr>
        <td>assignee_id</td>
        <td>Unique identifier for the assignee.</td>
        <td>string</td>
      </tr><tr>
        <td>assignee_type</td>
        <td>Classification of assignee (1 - Unassigned, 2 - US Company or Corporation, 3 - Foreign Company or Corporation, 4 - US Individual, 5 - Foreign Individual, 6 - US Federal Government, 7 - Foreign Government, 8 - US County Government, 9 - US State Government. Note: A "1" appearing before any of these codes signifies part interest.</td>
        <td>string</td>
      </tr><tr>
        <td>assignee_individual_name_first</td>
        <td>First name, if assignee is an individual.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_individual_name_last</td>
        <td>Last name, if assignee is an individual.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_organization</td>
        <td>Organization name if assignee is an organization.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_city</td>
        <td>City where the assignee is located.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_state</td>
        <td>State where the assignee is located.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_country</td>
        <td>Country where the assignee is located.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_sequence</td>
        <td>Order in which assignee appears in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>attorneys</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr>
      <tr>
        <td>attorney_id</td>
        <td>Unique identifier for the attorney.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>attorney_name_first</td>
        <td>First name of the attorney.</td>
        <td>text</td>
      </tr>
      <tr>
        <td>attorney_name_last</td>
        <td>Last name of the attorney.</td>
        <td>text</td>
      </tr>
      <tr>
        <td>attorney_organization</td>
        <td>Name of the organization if the attorney is part of a law firm or company.</td>
        <td>text</td>
      </tr>
      <tr>
        <td>attorney_sequence</td>
        <td>Order in which the attorney appears in the patent file; the values for this field begin at 0.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>botanic</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr>
      <tr>
        <td>latin_name</td>
        <td>Latin name of the botanical variety.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>variety</td>
        <td>Specific variety of the botanical specimen.</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>cpc_at_issue</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>cpc_class</td>
        <td>CPC classification code.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_class_id</td>
        <td>ID for the CPC class.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_group</td>
        <td>CPC “group” symbol for further subdivision.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_group_id</td>
        <td>ID for the CPC group.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_section</td>
        <td>High-level CPC section code.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_subclass</td>
        <td>CPC “subclass” symbol.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_subclass_id</td>
        <td>ID for the CPC subclass.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_sequence</td>
        <td>Order in which cpc class appears in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr><tr>
        <td>cpc_type</td>
        <td>CPC category (inventional or additional).</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>cpc_current</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>cpc_class</td>
        <td>CPC “class” symbol.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_class_id</td>
        <td>ID for the CPC class.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_group</td>
        <td>ID for the CPC group.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_group_id</td>
        <td>ID for the CPC group.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_section</td>
        <td>High-level CPC section code.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_subclass</td>
        <td>CPC “subclass” symbol.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_subclass_id</td>
        <td>ID for the CPC subclass.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_sequence</td>
        <td>Order in which cpc class appears in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr><tr>
        <td>cpc_type</td>
        <td>CPC category (inventional or additional).</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>examiners</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>examiner_id</td>
        <td>Identifier for the examiner.</td>
        <td>string</td>
      </tr><tr>
        <td>examiner_first_name</td>
        <td>First name of the examiner.</td>
        <td>text</td>
      </tr><tr>
        <td>examiner_last_name</td>
        <td>Last name of the examiner.</td>
        <td>text</td>
      </tr><tr>
        <td>examiner_role</td>
        <td>Role of the examiner.</td>
        <td>string</td>
      </tr><tr>
        <td>art_group</td>
        <td>Art unit, tech center, industry sector, or other grouping of US patent examiners.</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>figures</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>num_figures</td>
        <td>Number of figures included with patent.</td>
        <td>integer</td>
      </tr><tr>
        <td>num_sheets</td>
        <td>Number of drawing sheets included with patent.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>foreign_priority</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>filing_date</td>
        <td>Filing date of the foreign priority application.</td>
        <td>date</td>
      </tr><tr>
        <td>foreign_application_id</td>
        <td>Foreign patent application number.</td>
        <td>string</td>
      </tr><tr>
        <td>foreign_country_filed</td>
        <td>Country code for the country in which patent was originally filed. (ISO 3166-1 alpha-2 codes)</td>
        <td>string</td>
      </tr><tr>
        <td>priority_claim_kind</td>
        <td>Type of priority claim (international, national, regional).</td>
        <td>string</td>
      </tr><tr>
        <td>priority_claim_sequence</td>
        <td>Order in which priority claims appear in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>gov_interest_contract_award_numbers</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>award_number</td>
        <td>Federal contract award number.</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>gov_interest_organizations</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>fedagency_name</td>
        <td>Name of Federal department, agency, bureau, etc.</td>
        <td>text</td>
      </tr><tr>
      <!-- Note: may want to make these definitions simpler and clearer -->
        <td>level_one</td>
        <td>The position of the focal agency in a hierarchical set of relationships with the parent agency (e.g., DHHS) at level_one and child agencies at lower levels (e.g., NIH at level_two and NIDA at level_three). Government_organization.name is equal to one and only one of the values in level_one, level_two, and level_three.</td>
        <td>text</td>
      </tr><tr>
        <td>level_two</td>
        <td>The position of the focal agency in a hierarchical set of relationships with the parent agency (e.g., DHHS) at level_one and child agencies at lower levels (e.g., NIH at level_two and NIDA at level_three). Government_organization.name is equal to one and only one of the values in level_one, level_two, and level_three.</td>
        <td>text</td>
      </tr><tr>
        <td>level_three</td>
        <td>The position of the focal agency in a hierarchical set of relationships with the parent agency (e.g., DHHS) at level_one and child agencies at lower levels (e.g., NIH at level_two and NIDA at level_three). Government_organization.name is equal to one and only one of the values in level_one, level_two, and level_three.</td>
        <td>text</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>granted_pregrant_crosswalk</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
      <!-- Note: this should be changed to application_id for consistency with the application subfield, but this will require edits to endpoint and elastic index. -->
        <td>application_number</td>
        <td>The ID for the patent application corresponding to this patent.</td>
        <td>string</td>
      </tr><tr>
        <td>document_number</td>
        <td>The ID for the pre-grant publication of the application corresponding to this patent.</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>inventors</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>inventor</td>
        <td>An API request URL for additional details of this inventor.</td>
        <td>string</td>
      </tr><tr>
        <td>inventor_id</td>
        <td>Unique identifier for the inventor in the database.</td>
        <td>string</td>
      </tr><tr>
        <td>inventor_name_first</td>
        <td>Inventor’s given (first) name, if an individual.</td>
        <td>text</td>
      </tr><tr>
        <td>inventor_name_last</td>
        <td>Inventor’s given (last) name, if an individual.</td>
        <td>text</td>
      </tr><tr>
        <td>inventor_gender_code</td>
        <td>A code representing the most likely gender of the inventor, based on the individual's name and country of residence. (F = female, M = male, U = no gender attributed)</td>
        <td>string</td>
      </tr><tr>
        <td>inventor_city</td>
        <td>City where the inventor resides or is located.</td>
        <td>text</td>
      </tr><tr>
        <td>inventor_state</td>
        <td>State where the inventor resides or is located.</td>
        <td>text</td>
      </tr><tr>
        <td>inventor_country</td>
        <td>Country where the inventor resides or is located.</td>
        <td>text</td>
      </tr><tr>
        <td>inventor_location_id</td>
        <td>Identifier referencing the inventor’s location record.</td>
        <td>string</td>
      </tr><tr>
        <td>inventor_sequence</td>
        <td>Order in which inventor appears in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>ipcr</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>ipc_id</td>
        <td>PatentsView-internal ID for the IPC entry.</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_action_date</td>
        <td>Date associated with the IPC classification event.</td>
        <td>date</td>
      </tr><tr>
        <td>ipc_class</td>
        <td>Class portion of the International Patent Classification.</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_classification_data_source</td>
        <td>Source of the IPC classification data.</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_classification_value</td>
        <td>Additional classification value or level of detail.</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_main_group</td>
        <td>Main group code in the IPC subclass level (e.g., G06F 3/00).</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_section</td>
        <td>Top-level IPC section (A–H).</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_subclass</td>
        <td>Specific subclass symbol within the section (e.g., G06).</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_subgroup</td>
        <td>Detailed subgroup code (e.g., 3/06).</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_sequence</td>
        <td>Order in which ipc class appears in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr><tr>
        <td>ipc_symbol_position</td>
        <td>Indicates whether the classification is the primary (invention) or secondary (additional).</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>pct_data</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>application_kind</td>
        <td>Kind of application (numerical for filed, alphabetical for published).</td>
        <td>string</td>
      </tr><tr>
        <td>pct_102_date</td>
        <td>Effective date for 35 U.S.C. 102(e) prior art under the PCT process.</td>
        <td>date</td>
      </tr><tr>
        <td>pct_371_date</td>
        <td>Date of entering the national stage under 35 U.S.C. 371.</td>
        <td>date</td>
      </tr><tr>
        <td>pct_doc_number</td>
        <td>ID of pct patent.</td>
        <td>string</td>
      </tr><tr>
        <td>pct_doc_type</td>
        <td>Whether the document has been published or just filed.</td>
        <td>string</td>
      </tr><tr>
        <td>published_filed_date</td>
        <td>Date the PCT application was published.</td>
        <td>date</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>us_related_documents</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>published_country</td>
        <td>Country where related document was published.</td>
        <td>string</td>
      </tr><tr>
        <td>related_doc_kind</td>
        <td>Kind of document.</td>
        <td>string</td>
      </tr><tr>
        <td>related_doc_number</td>
        <td>Related document number.</td>
        <td>string</td>
      </tr><tr>
        <td>related_doc_published_date</td>
        <td>Publication date of the related document.</td>
        <td>date</td>
      </tr><tr>
        <td>related_doc_sequence</td>
        <td>Order in which the related document appears in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr><tr>
        <td>related_doc_status</td>
        <td>Status of related document.</td>
        <td>string</td>
      </tr><tr>
        <td>related_doc_type</td>
        <td>Defines the type of documentation.</td>
        <td>string</td>
      </tr><tr>
        <td>wipo_kind</td>
        <td>WIPO document kind codes (http://www.uspto.gov/learning-and-resources/support-centers/electronic-business-center/kind-codes-included-uspto-patent) .</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>us_term_of_grant</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>disclaimer_date</td>
        <td>Date of the terminal disclaimer.</td>
        <td>date</td>
      </tr><tr>
        <td>term_disclaimer</td>
        <td>Disclaimer if the patent is subject to a terminal disclaimer.</td>
        <td>string</td>
      </tr><tr>
        <td>term_extension</td>
        <td>An extension to the grant term period in days.</td>
        <td>string</td>
      </tr><tr>
        <td>term_grant</td>
        <td>The length of time during which a patent is in force (i.e. the inventor(s) or assignee(s) have exclusive rights to the invention) in years.</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>uspc_at_issue</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>uspc_mainclass</td>
        <td>An API request URL for additional details of this mainclass.</td>
        <td>string</td>
      </tr><tr>
        <td>uspc_mainclass_id</td>
        <td>The code for the main U.S. Patent Classification at issue.</td>
        <td>string</td>
      </tr><tr>
        <td>uspc_sequence</td>
        <td>Order in which uspc class appears in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr><tr>
        <td>uspc_subclass</td>
        <td>An API request URL for additional details of this subclass.</td>
        <td>string</td>
      </tr><tr>
        <td>uspc_subclass_id</td>
        <td>The subclass portion of the U.S. classification.</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>wipo</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>wipo_field</td>
        <td>An API request URL for additional details of this WIPO technology field.</td>
        <td>string</td>
      </tr><tr>
        <td>wipo_field_id</td>
        <td>The code identifying the WIPO technology field.</td>
        <td>string</td>
      </tr><tr>
        <td>wipo_sequence</td>
        <td>Order in which WIPO technology field appears on patent; values for this field begin at 0.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
</details>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"patent_id":"asc"&#125;]<br/>
  </div>
</details>

### US Patent Citations

<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/patent/us_patent_citation/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;us_patent_citations</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>patent_id</td>
        <td>The ID of the citing patent.</td>
        <td>string</td>
      </tr><tr>
        <td>patent</td>
        <td>An API request URL for additional details of the citing patent.</td>
        <td>string</td>
      </tr><tr>
        <td>citation_patent_id</td>
        <td>The ID of the patent being cited.</td>
        <td>string</td>
      </tr><tr>
        <td>citation_patent</td>
        <td>An API request URL for additional details of the patent being cited.</td>
        <td>string</td>
      </tr><tr>
        <td>citation_category</td>
        <td>Who cited the patent (examiner, applicant, other etc).</td>
        <td>string</td>
      </tr><tr>
        <td>citation_date</td>
        <td>First day of the month the cited patent (citation_id) was published.</td>
        <td>date</td>
      </tr><tr>
        <td>citation_name</td>
        <td>Name associated with the citation.</td>
        <td>string</td>
      </tr><tr>
        <td>citation_sequence</td>
        <td>Order in which this citation is cited by patent; values for this field begin at 0.</td>
        <td>integer</td>
      </tr><tr>
        <td>citation_wipo_kind</td>
        <td>WIPO document kind codes associated with the citation.</td>
        <td>string</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"patent_id":"asc"&#125;,&#123;"citation_sequence":"asc"&#125;]<br/>
  </div>
</details>

### US Application Citations

<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/patent/us_application_citation/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;us_application_citations</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>patent_id</td>
        <td>The ID of the citing patent.</td>
        <td>string</td>
      </tr><tr>
        <td>patent</td>
        <td>An API request URL for additional details of the citing patent.</td>
        <td>string</td>
      </tr><tr>
        <td>citation_document_number</td>
        <td>Publication number of the pre-grant publication being cited.</td>
        <td>string</td>
      </tr><tr>
        <td>citation_sequence</td>
        <td>Order in which this reference is cited by select patent; values for this field begin at 0.</td>
        <td>integer</td>
      </tr><tr>
        <td>citation_category</td>
        <td>Who cited the patent (examiner, applicant, other etc).</td>
        <td>string</td>
      </tr><tr>
        <td>citation_date</td>
        <td>First day of the month the cited patent (citation_id) was published.</td>
        <td>date</td>
      </tr><tr>
        <td>citation_name</td>
        <td>Name associated with the citation.</td>
        <td>string</td>
      </tr><tr>
        <td>citation_wipo_kind</td>
        <td>WIPO document kind codes associated with the citation.</td>
        <td>string</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"patent_id":"asc"&#125;,&#123;"citation_sequence":"asc"&#125;]<br/>
  </div>
</details>

### Foreign Citations

<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/patent/foreign_citation/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;foreign_citations</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>patent_id</td>
        <td>The ID of the citing patent.</td>
        <td>string</td>
      </tr><tr>
        <td>patent</td>
        <td>An API request URL for additional details of the citing patent.</td>
        <td>string</td>
      </tr><tr>
        <td>citation_number</td>
        <td>The document number of the cited foreign patent.</td>
        <td>string</td>
      </tr><tr>
        <td>citation_sequence</td>
        <td>Order in which this citation is cited by patent; values for this field begin at 0.</td>
        <td>integer</td>
      </tr><tr>
        <td>citation_date</td>
        <td>First day of the month the cited patent (citation_id) was published.</td>
        <td>date</td>
      </tr><tr>
        <td>citation_category</td>
        <td>Who cited the patent (examiner, applicant, other etc).</td>
        <td>string</td>
      </tr><tr>
        <td>citation_country</td>
        <td>Country patent/application (number) was filed.</td>
        <td>string</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"patent_id":"asc"&#125;,&#123;"citation_sequence":"asc"&#125;]<br/>
  </div>
</details>

### Other Reference

<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/patent/other_reference/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;other_references</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>patent_id</td>
        <td>The ID of the citing patent.</td>
        <td>string</td>
      </tr><tr>
        <td>patent</td>
        <td>An API request URL for additional details of the citing patent.</td>
        <td>string</td>
      </tr><tr>
        <td>reference_sequence</td>
        <td>The order in which this reference appears in the raw XML for the current patent; sequence for this table starts at 0.</td>
        <td>string</td>
      </tr><tr>
        <td>reference_text</td>
        <td>The text of the reference to a non-patent, non-application source.</td>
        <td>text</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"patent_id":"asc"&#125;,&#123;"reference_sequence":"asc"&#125;]<br/>
  </div>
</details>

### Related Application Text (Patent)

<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/patent/rel_app_text/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;rel_app_texts</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>patent_id</td>
        <td>Patent number.</td>
        <td>string</td>
      </tr><tr>
        <td>related_text</td>
        <td>The text of the description of the related application in question.</td>
        <td>text</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"patent_id":"asc"&#125;]<br/>
  </div>
</details>

## Pre-grant Publication Endpoints

### Publication

<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/publication/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;publications</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr>
      <tr>
        <td>document_number</td>
        <td>Unique identifier for the pre-grant publication.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>publication_abstract</td>
        <td>Abstract text of the publication.</td>
        <td>text</td>
      </tr>
      <tr>
        <td>publication_date</td>
        <td>Date when the publication was made available.</td>
        <td>date</td>
      </tr>
      <tr>
        <td>publication_title</td>
        <td>Title of the publication.</td>
        <td>text</td>
      </tr>
      <tr>
        <td>publication_type</td>
        <td>Type or category of the publication (e.g., journal, conference).</td>
        <td>string</td>
      </tr>
      <tr>
        <td>publication_year</td>
        <td>Year when the publication was made available.</td>
        <td>integer</td>
      </tr>
      <tr>
        <td>rule_47_flag</td>
        <td>Flag for the inventor who was unable to be contacted at the time of filing.</td>
        <td>boolean</td>
      </tr>
      <tr>
        <td>series_code</td>
        <td>Application series; "D" for some designs; (http://www.uspto.gov/web/offices/ac/ido/oeip/taf/filingyr.htm)</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
<h4>Nested Fields</h4>
  <div>
<details>
  <summary>assignees</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>assignee</td>
        <td>An API request URL for additional details of this assignee.</td>
        <td>string</td>
      </tr><tr>
        <td>assignee_id</td>
        <td>Unique identifier for the assignee.</td>
        <td>string</td>
      </tr><tr>
        <td>assignee_type</td>
        <td>Classification of assignee (1 - Unassigned, 2 - US Company or Corporation, 3 - Foreign Company or Corporation, 4 - US Individual, 5 - Foreign Individual, 6 - US Federal Government, 7 - Foreign Government, 8 - US County Government, 9 - US State Government. Note: A "1" appearing before any of these codes signifies part interest.</td>
        <td>string</td>
      </tr><tr>
        <td>assignee_individual_name_first</td>
        <td>First name, if assignee is an individual.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_individual_name_last</td>
        <td>Last name, if assignee is an individual.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_organization</td>
        <td>Organization name if assignee is an organization.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_city</td>
        <td>City where the assignee is located.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_state</td>
        <td>State where the assignee is located.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_country</td>
        <td>Country where the assignee is located.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_location_id</td>
        <td>Identifier referencing the assignee’s location record.</td>
        <td>string</td>
      </tr><tr>
        <td>assignee_sequence</td>
        <td>Order in which assignee appears in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>cpc_at_issue</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr>
      <tr>
        <td>action_date</td>
        <td>Date on which a change or revision to the CPC classification scheme becomes effective.</td>
        <td>date</td>
      </tr>
      <tr>
        <td>cpc_class</td>
        <td>CPC “class” symbol.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>cpc_class_id</td>
        <td>ID for the CPC class.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>cpc_group</td>
        <td>CPC group symbol for further subdivision.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>cpc_group_id</td>
        <td>ID for the CPC group.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>cpc_section</td>
        <td>High-level CPC section code.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>cpc_sequence</td>
        <td>Order in which the CPC class appears in the patent file; the values for this field begin at 0.</td>
        <td>integer</td>
      </tr>
      <tr>
        <td>cpc_subclass</td>
        <td>CPC “subclass” symbol.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>cpc_subclass_id</td>
        <td>ID for the CPC subclass.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>cpc_type</td>
        <td>CPC category (inventional or additional).</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>cpc_current</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr>
      <tr>
        <td>cpc_class</td>
        <td>CPC “class” symbol.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>cpc_class_id</td>
        <td>ID for the CPC class.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>cpc_group</td>
        <td>CPC group symbol for further subdivision.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>cpc_group_id</td>
        <td>ID for the CPC group.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_section</td>
        <td>CPC section (A = Human Necessitates, B = Performing Operations; Transporting, C = Chemistry; Metallurgy, D = Textiles; Paper, E = Fixed Constructions, F = Mechanical Engineering; Lighting; Heating; Weapons; Blasting Engines or Pumps, G = Physics, H = Electricity, Y = General Tagging of New Technological Developments).</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_sequence</td>
        <td>Order in which cpc class appears in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr><tr>
        <td>cpc_subclass</td>
        <td>CPC “subclass” symbol.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>cpc_subclass_id</td>
        <td>ID for the CPC subclass.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_type</td>
        <td>CPC category (inventional or additional).</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>foreign_priority</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>filing_date</td>
        <td>Filing date of the foreign priority application.</td>
        <td>date</td>
      </tr><tr>
        <td>foreign_application_id</td>
        <td>Foreign patent application number.</td>
        <td>string</td>
      </tr><tr>
        <td>foreign_country_filed</td>
        <td>Country in which patent was originally filed (transformed name).</td>
        <td>string</td>
      </tr><tr>
        <td>priority_claim_kind</td>
        <td>Type of priority claim (international, national, regional).</td>
        <td>string</td>
        </tr><tr>
        <td>priority_claim_sequence</td>
        <td>Order in which priority claims appear in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>gov_interest_organizations</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr>
      <tr>
        <td>fedagency_name</td>
        <td>Name of Federal department, agency, bureau, etc.</td>
        <td>text</td>
      </tr>
      <tr>
        <td>level_one</td>
        <td>Position of the focal agency in a hierarchical set of relationships, with the parent agency (e.g., DHHS) at level_one and child agencies at lower levels (e.g., NIH at level_two and NIDA at level_three). government_organization.name is equal to one and only one of the values in level_one, level_two, and level_three.</td>
        <td>text</td>
      </tr>
      <tr>
        <td>level_two</td>
        <td>Position of the focal agency in a hierarchical set of relationships, with the parent agency (e.g., DHHS) at level_one and child agencies at lower levels (e.g., NIH at level_two and NIDA at level_three). government_organization.name is equal to one and only one of the values in level_one, level_two, and level_three.</td>
        <td>text</td>
      </tr>
      <tr>
        <td>level_three</td>
        <td>Position of the focal agency in a hierarchical set of relationships, with the parent agency (e.g., DHHS) at level_one and child agencies at lower levels (e.g., NIH at level_two and NIDA at level_three). government_organization.name is equal to one and only one of the values in level_one, level_two, and level_three.</td>
        <td>text</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>granted_pregrant_crosswalk</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>application_number</td>
        <td>The U.S. application number linking the granted patent to its pre-grant publication.</td>
        <td>string</td>
      </tr><tr>
        <td>current_document_number_flag</td>
        <td>Documentation number Flag for the current publication.</td>
        <td>boolean</td>
      </tr><tr>
        <td>current_patent_id_flag</td>
        <td>Patent ID Flag for the current Publication.</td>
        <td>boolean</td>
      </tr><tr>
        <td>patent_id</td>
        <td>Patent number.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>inventors</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>inventor</td>
        <td>An API request URL for additional details of this inventor.</td>
        <td>string</td>
      </tr><tr>
        <td>inventor_id</td>
        <td>Unique identifier for the inventor in the database.</td>
        <td>string</td>
      </tr><tr>
        <td>inventor_name_first</td>
        <td>Inventor’s given first name, if an individual.</td>
        <td>text</td>
      </tr><tr>
        <td>inventor_name_last</td>
        <td>Inventor’s given last name, if an individual.</td>
        <td>text</td>
      </tr><tr>
        <td>inventor_gender_code</td>
        <td>Code (if present) indicating the inventor’s gender.</td>
        <td>string</td>
      </tr><tr>
        <td>inventor_city</td>
        <td>City where the inventor resides or is located.</td>
        <td>text</td>
      </tr><tr>
        <td>inventor_state</td>
        <td>State where the inventor resides or is located.</td>
        <td>text</td>
      </tr><tr>
        <td>inventor_country</td>
        <td>Country where the inventor resides or is located.</td>
        <td>text</td>
      </tr><tr>
        <td>inventor_location_id</td>
        <td>Identifier referencing the inventor’s location record.</td>
        <td>string</td>
      </tr><tr>
        <td>inventor_sequence</td>
        <td>Order in which the inventor is listed on the patent.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>ipcr</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>ipc_action_date</td>
        <td>Date associated with the IPC classification event.</td>
        <td>date</td>
      </tr><tr>
        <td>ipc_class</td>
        <td>Class portion of the International Patent Classification.</td>
        <td>string</td>
      </tr><tr>
      <td>ipc_class_level</td>
        <td>Class Level of the International Patent Classification.</td>
        <td>string</td>
      </tr><tr>
      <td>ipc_class_status</td>
        <td>Class Status of the International Patent Classification.</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_classification_data_source</td>
        <td>Source of the IPC classification data.</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_classification_value</td>
        <td>Additional classification value or level of detail.</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_id</td>
        <td>PatentsView-internal ID for the IPC entry.</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_main_group</td>
        <td>Main group code in the IPC subclass level (e.g., G06F 3/00).</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_section</td>
        <td>Top-level IPC section (A–H).</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_sequence</td>
        <td>Order in which ipc class appears in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr><tr>
        <td>ipc_subclass</td>
        <td>Specific subclass symbol within the section (e.g., G06).</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_subgroup</td>
        <td>Detailed subgroup code (e.g., 3/06).</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_symbol_position</td>
        <td>Indicates whether the classification is the primary (invention) or secondary (additional).</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>pct_data</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>application_kind</td>
        <td>Kind of application (numerical for filed, alphabetical for published).</td>
        <td>string</td>
      </tr><tr>
        <td>filed_country</td>
        <td>Country in which the PCT application was filled by the inventor.</td>
        <td>string</td>
      </tr><tr>
        <td>pct_102_date</td>
        <td>Date on which the PCT application entered the national stage under 35 U.S.C. § 102(e).</td>
        <td>date</td>
      </tr><tr>
        <td>pct_371_date</td>
        <td>PCT Section 371(c)(1)(2)(4)date, date when pct application was filed.</td>
        <td>date</td>
      </tr><tr>
        <td>pct_doc_number</td>
        <td>ID of the PCT patent.</td>
        <td>string</td>
      </tr><tr>
        <td>pct_doc_type</td>
        <td>Whether the document has been published or just filed.</td>
        <td>string</td>
      </tr><tr>
        <td>published_filed_date</td>
        <td>Date on which the PCT application was published or filed.</td>
        <td>date</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>us_related_documents</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>published_country</td>
        <td>Country where related document was published.</td>
        <td>string</td>
      </tr><tr>
        <td>related_doc_kind</td>
        <td>Kind of document (text).</td>
        <td>string</td>
      </tr><tr>
        <td>related_doc_number</td>
        <td>Related document number.</td>
        <td>string</td>
      </tr><tr>
        <td>related_doc_published_date</td>
        <td>Publication date of the related document.</td>
        <td>date</td>
        </tr><tr>
        <td>related_doc_sequence</td>
        <td>Order in which the related document appears in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr><tr>
        <td>related_doc_type</td>
        <td>The nature of the relationship between the publication and related document.</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>us_parties</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr>
      <tr>
        <td>applicant_authority</td>
        <td>The type of interest the applicant has in the intellectual property rights of a patent or application. May be equal to inventor, legal-representative, party-of-interest, obligated-assignee, assignee, or null..</td>
        <td>string</td>
      </tr>
      <tr>
        <td>location_id</td>
        <td>Identifier referencing the location record of the US party.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>us_party_designation</td>
        <td>Designation or role of the US party (e.g., inventor, assignee).</td>
        <td>string</td>
      </tr>
      <tr>
        <td>us_party_name_first</td>
        <td>First name of the US party.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>us_party_name_last</td>
        <td>Last name of the US party.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>us_party_organization</td>
        <td>Name of the organization, if the US party is an organization.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>us_party_sequence</td>
        <td>Order in which the US party is listed in the record; the values for this field begin at 0.</td>
        <td>integer</td>
      </tr>
      <tr>
        <td>us_party_type</td>
        <td>Type of party record, indicating whether it is an individual or organization.</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>uspc_at_issue</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>uspc_mainclass</td>
        <td>An API request URL for additional details of this mainclass.</td>
        <td>string</td>
      </tr><tr>
        <td>uspc_mainclass_id</td>
        <td>The ID for the main U.S. Patent Classification at issue.</td>
        <td>string</td>
      </tr><tr>
        <td>uspc_sequence</td>
        <td>Order in which uspc class appears in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr><tr>
        <td>uspc_subclass</td>
        <td>An API request URL for additional details of this subclass.</td>
        <td>string</td>
      </tr><tr>
        <td>uspc_subclass_id</td>
        <td>The subclass portion of the U.S. classification.</td>
        <td>string</td>
      </tr>
    </table>
  </div>
</details>
<details>
  <summary>wipo</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>wipo_field</td>
        <td>Name or label of the WIPO technology field.</td>
        <td>string</td>
      </tr><tr>
        <td>wipo_field_id</td>
        <td>The code identifying the WIPO technology field.</td>
        <td>string</td>
      </tr><tr>
        <td>wipo_sector_title</td>
        <td>WIPO technology sector title.</td>
        <td>string</td>
      </tr><tr>
        <td>wipo_sequence</td>
        <td>Order in which WIPO technology field appears on patent; values for this field begin at 0.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
</details>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"document_number":"asc"&#125;]<br/>
  </div>
</details>

### Related Application Text (Publication)

<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/publication/rel_app_text/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;rel_app_texts</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>document_number</td>
        <td>Unique identifier for the related application publication.</td>
        <td>string</td>
      </tr><tr>
        <td>related_text</td>
        <td>Text related to the application, providing additional details or context.</td>
        <td>text</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"document_number":"asc"&#125;]<br/>
  </div>
</details>


## Common Endpoints
The endpoints in this section relate to both granted patent and pregrant publications. These function as lookup endpoints for entities disambiguated by PatentsView disambiguation algorithm.

### Assignee
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/assignee/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;assignees</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>assignee_id</td>
        <td>Unique identifier for the assignee.</td>
        <td>string</td>
      </tr><tr>
        <td>assignee_individual_name_first</td>
        <td>First name of the assignee, if an individual.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_individual_name_last</td>
        <td>Last name of the assignee, if an individual.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_organization</td>
        <td>Name of the organization, if the assignee is an organization.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_type</td>
        <td>Type of assignee (e.g., individual, corporate).</td>
        <td>string</td>
      </tr><tr>
        <td>assignee_lastknown_city</td>
        <td>City where the assignee was last known to reside or be located.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_lastknown_state</td>
        <td>State where the assignee was last known to reside or be located.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_lastknown_country</td>
        <td>Country where the assignee was last known to reside or be located.</td>
        <td>text</td>
      </tr><tr>
        <td>assignee_lastknown_latitude</td>
        <td>Latitude of the last known location of the assignee.</td>
        <td>double</td>
      </tr><tr>
        <td>assignee_lastknown_longitude</td>
        <td>Longitude of the last known location of the assignee.</td>
        <td>double</td>
      </tr><tr>
        <td>assignee_lastknown_location</td>
        <td>Location identifier for the last known location of the assignee.</td>
        <td>string</td>
      </tr><tr>
        <td>assignee_first_seen_date</td>
        <td>Date when the assignee was first recorded.</td>
        <td>date</td>
      </tr><tr>
        <td>assignee_last_seen_date</td>
        <td>Date when the assignee was last recorded.</td>
        <td>date</td>
      </tr><tr>
        <td>assignee_num_inventors</td>
        <td>Number of inventors associated with the assignee.</td>
        <td>integer</td>
      </tr><tr>
        <td>assignee_num_patents</td>
        <td>Number of patents associated with the assignee.</td>
        <td>integer</td>
      </tr><tr>
        <td>assignee_years_active</td>
        <td>Number of years the assignee has been active.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
<h4>Nested Fields</h4>
  <div>
<details>
  <summary>assignee_years</summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>year</td>
        <td>The year in which the patents were recorded.</td>
        <td>integer</td>
      </tr><tr>
        <td>num_patents</td>
        <td>The number of patents recorded in the specified year.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
</details>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"assignee_id":"asc"&#125;]<br/>
  </div>
</details>

### Attorney
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/patent/attorney/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;attorneys</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr>
      <tr>
        <td>attorney_id</td>
        <td>Unique identifier for the attorney.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>attorney_name_first</td>
        <td>First name of the attorney.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>attorney_name_last</td>
        <td>Last name of the attorney.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>attorney_organization</td>
        <td>Name of the organization the attorney is associated with.</td>
        <td>text</td>
      </tr>
      <tr>
        <td>attorney_first_seen_date</td>
        <td>Date when the attorney was first recorded.</td>
        <td>date</td>
      </tr>
      <tr>
        <td>attorney_last_seen_date</td>
        <td>Date when the attorney was last recorded.</td>
        <td>date</td>
      </tr>
      <tr>
        <td>attorney_num_inventors</td>
        <td>Number of inventors associated with the attorney.</td>
        <td>integer</td>
      </tr>
      <tr>
        <td>attorney_num_patents</td>
        <td>Number of patents associated with the attorney.</td>
        <td>integer</td>
      </tr>
      <tr>
        <td>attorney_years_active</td>
        <td>Number of years the attorney has been active.</td>
        <td>integer</td>
      </tr>
    </table>
    <hr/>
    <b>default sort:</b>&emsp;[&#123;"attorney_id":"asc"&#125;]<br/>
  </div>
</details>

### Inventor
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/inventor/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;inventors</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr>
      <tr>
        <td>inventor_id</td>
        <td>Unique identifier for the inventor.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>inventor_name_first</td>
        <td>First name of the inventor.</td>
        <td>text</td>
      </tr>
      <tr>
        <td>inventor_name_last</td>
        <td>Last name of the inventor.</td>
        <td>text</td>
      </tr>
      <tr>
        <td>inventor_gender_code</td>
        <td>Code representing the inventor's gender.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>inventor_lastknown_city</td>
        <td>City where the inventor was last known to reside or be located.</td>
        <td>text</td>
      </tr>
      <tr>
        <td>inventor_lastknown_state</td>
        <td>State where the inventor was last known to reside or be located.</td>
        <td>text</td>
      </tr>
      <tr>
        <td>inventor_lastknown_country</td>
        <td>Country where the inventor was last known to reside or be located.</td>
        <td>text</td>
      </tr>
      <tr>
        <td>inventor_lastknown_latitude</td>
        <td>Latitude of the last known location of the inventor.</td>
        <td>double</td>
      </tr>
      <tr>
        <td>inventor_lastknown_longitude</td>
        <td>Longitude of the last known location of the inventor.</td>
        <td>double</td>
      </tr>
      <tr>
        <td>inventor_lastknown_location</td>
        <td>Location identifier for the last known location of the inventor.</td>
        <td>string</td>
      </tr>
      <tr>
        <td>inventor_first_seen_date</td>
        <td>Date when the inventor was first recorded.</td>
        <td>date</td>
      </tr>
      <tr>
        <td>inventor_last_seen_date</td>
        <td>Date when the inventor was last recorded.</td>
        <td>date</td>
      </tr>
      <tr>
        <td>inventor_num_assignees</td>
        <td>Number of assignees associated with the inventor.</td>
        <td>integer</td>
      </tr>
      <tr>
        <td>inventor_num_patents</td>
        <td>Number of patents associated with the inventor.</td>
        <td>integer</td>
      </tr>
      <tr>
        <td>inventor_years_active</td>
        <td>Number of years the inventor has been active.</td>
        <td>integer</td>
      </tr>
    </table>
  </div>
  <h4>Nested Fields</h4>
  <div>
    <details>
      <summary>inventor_years</summary>
      <div>
        <table>
          <tr>
            <th>Field Name</th>
            <th>Description</th>
            <th>Data Type</th>
          </tr>
          <tr>
            <td>year</td>
            <td>The year in which the patents were recorded.</td>
            <td>integer</td>
          </tr>
          <tr>
            <td>num_patents</td>
            <td>The number of patents recorded in the specified year.</td>
            <td>integer</td>
          </tr>
        </table>
      </div>
    </details>
    <hr/>
    <b>default sort:</b>&emsp;[&#123;"inventor_id":"asc"&#125;]<br/>
  </div>
</details>

### Location

<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/location/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;locations</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>location_id</td>
        <td>Unique identifier for the location.</td>
        <td>string</td>
      </tr><tr>
        <td>location_name</td>
        <td>Name of the location (city or place).</td>
        <td>text</td>
      </tr><tr>
        <td>location_county</td>
        <td>County where the location is situated.</td>
        <td>text</td>
      </tr><tr>
        <td>location_county_fips</td>
        <td>FIPS code for the county.</td>
        <td>string</td>
      </tr><tr>
        <td>location_state</td>
        <td>State where the location is situated.</td>
        <td>text</td>
      </tr><tr>
        <td>location_state_fips</td>
        <td>FIPS code for the State.</td>
        <td>string</td>
      </tr><tr>
        <td>location_country</td>
        <td>Country where the location is situated.</td>
        <td>text</td>
      </tr><tr>
        <td>location_place_type</td>
        <td>Type of the place.</td>
        <td>string</td>
      </tr><tr>
        <td>location_latitude</td>
        <td>Latitude of the location.</td>
        <td>double</td>
      </tr>
      <tr>
        <td>location_longitude</td>
        <td>Longitude of the location.</td>
        <td>double</td>
      </tr><tr>
        <td>location_num_assignees</td>
        <td>Number of assignees associated with the location.</td>
        <td>integer</td>
      </tr><tr>
        <td>location_num_patents</td>
        <td>Number of patents associated with the location.</td>
        <td>integer</td>
      </tr><tr>
        <td>location_num_inventors</td>
        <td>Number of inventors associated with the location.</td>
        <td>integer</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"location_id":"asc"&#125;]<br/>
  </div>
</details>

## Classification Details

### CPC Class/Subclass/Group
<!-- cpc class -->
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/cpc_class/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;cpc_classes</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>cpc_class_id</td>
        <td>The three-character symbol for this Cooperative Patent Classification (CPC) Class.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_class_title</td>
        <td>The descriptive title for this CPC Class.</td>
        <td>text</td>
      </tr><tr>
        <td>cpc_class_first_seen_date</td>
        <td>The publication date for the first patent to be assigned this class.</td>
        <td>date</td>
      </tr><tr>
        <td>cpc_class_last_seen_date</td>
        <td>The publication date for the most recent patent to be assigned this class.</td>
        <td>date</td>
      </tr><tr>
        <td>cpc_class_years_active</td>
        <td>The length of time in years over which this class has been in use.</td>
        <td>integer</td>
      </tr><tr>
        <td>cpc_class_num_patents</td>
        <td>The number of patents to which this class is assigned.</td>
        <td>integer</td>
      </tr><tr>
        <td>cpc_class_num_assignees</td>
        <td>The number of unique assignees that appear on patents assigned this class.</td>
        <td>integer</td>
      </tr><tr>
        <td>cpc_class_num_inventors</td>
        <td>The number of unique inventors that appear on patents assigned this class.</td>
        <td>integer</td>
      </tr>
</table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"cpc_class_id":"asc"&#125;]<br/>
  </div>
</details>

<!-- cpc subclass -->
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/cpc_subclass/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;cpc_subclasses</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>cpc_subclass_id</td>
        <td>ID for the CPC subclass.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_subclass_title</td>
        <td>Title or description of the CPC subclass.</td>
        <td>text</td>
      </tr><tr>
        <td>cpc_class_id</td>
        <td>CPC “class” symbol.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_class</td>
        <td>Name or description of the CPC class.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_subclass_first_seen_date</td>
        <td>Date when the CPC subclass was first recorded.</td>
        <td>date</td>
      </tr><tr>
        <td>cpc_subclass_last_seen_date</td>
        <td>Date when the CPC subclass was last recorded.</td>
        <td>date</td>
      </tr><tr>
        <td>cpc_subclass_years_active</td>
        <td>Number of years the CPC subclass has been active.</td>
        <td>integer</td>
      </tr><tr>
        <td>cpc_subclass_num_patents</td>
        <td>Number of patents associated with the CPC subclass.</td>
        <td>integer</td>
      </tr><tr>
        <td>cpc_subclass_num_assignees</td>
        <td>Number of assignees associated with the CPC subclass.</td>
        <td>integer</td>
      </tr><tr>
        <td>cpc_subclass_num_inventors</td>
        <td>Number of inventors associated with the CPC subclass.</td>
        <td>integer</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"cpc_subclass_id":"asc"&#125;]<br/>
  </div>
</details>

<!-- cpc group -->
<details>
  <summary>
    <small><b>GET / POST</b></small>&emsp;/api/v1/cpc_group/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;cpc_groups</i></span>
  </summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>cpc_group_id</td>
        <td>Unique identifier for the CPC group.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_group_title</td>
        <td>Title or description of the CPC group.</td>
        <td>text</td>
      </tr><tr>
        <td>cpc_class</td>
        <td>Name or description of the CPC class.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_class_id</td>
        <td>CPC "class" symbol to which the group belongs.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_subclass</td>
        <td>Name or description of the CPC subclass to which the group belongs.</td>
        <td>string</td>
      </tr><tr>
        <td>cpc_subclass_id</td>
        <td>CPC "subclass" symbol to which the group belongs.</td>
        <td>string</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"cpc_group_id":"asc"&#125;]<br/>

  **Note:** CPC group values (`cpc_group_id`) typically contain the "/" character (for example, `A61K2039/892`). However, when using the single-id lookup endpoint `GET /api/v1/cpc_group/{cpc_group}/`, the URL parser does not accept "/".

  To use single-id lookup, substitute "/" with ":" (colon) in the value of `{cpc_group}`. For example: `A61K2039:892/`. This requirement applies only to GET requests of the form `/api/v1/cpc_group/{cpc_group}/`.
  </div>
</details>

### IPC

<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/ipc/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;ipcr</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>ipc_class</td>
        <td>Class portion of the International Patent Classification.</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_id</td>
        <td>PatentsView-internal ID for the IPC entry.</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_section</td>
        <td>Top-level IPC section (A–H).</td>
        <td>string</td>
      </tr><tr>
        <td>ipc_subclass</td>
        <td>Specific subclass symbol within the section (e.g., G06).</td>
        <td>string</td>
      </tr>
    </table>
  <hr/>
  <code><b>ipc_id</b></code> is a required field when providing the <code><b>f</b></code> field list.<br/>
  <b>default sort:</b>&emsp;[&#123;"ipc_id":"asc"&#125;]<br/>
  </div>
</details>

### USPC Mainclass/Subclass

<!-- uspc_mainclass -->
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/uspc_mainclass/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;uspc_mainclasses</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>uspc_mainclass_id</td>
        <td>ID for the main U.S. classification at issue.</td>
        <td>string</td>
      </tr><tr>
        <td>uspc_mainclass_title</td>
        <td>Description of uspc mainclass.</td>
        <td>text</td>
      </tr><tr>
        <td>uspc_mainclass_first_seen_date</td>
        <td>Date when the main U.S. classification was first recorded.</td>
        <td>date</td>
      </tr><tr>
        <td>uspc_mainclass_last_seen_date</td>
        <td>Date when the main U.S. classification was last recorded.</td>
        <td>date</td>
      </tr><tr>
        <td>uspc_mainclass_years_active</td>
        <td>Number of years the main U.S. classification has been active.</td>
        <td>integer</td>
      </tr><tr>
        <td>uspc_mainclass_num_patents</td>
        <td>Number of patents associated with the main U.S. classification.</td>
        <td>integer</td>
      </tr><tr>
        <td>uspc_mainclass_num_assignees</td>
        <td>Number of assignees associated with the main U.S. classification.</td>
        <td>integer</td>
      </tr><tr>
        <td>uspc_mainclass_num_inventors</td>
        <td>Number of inventors associated with the main U.S. classification.</td>
        <td>integer</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"uspc_mainclass_id":"asc"&#125;]<br/>
  </div>
</details>

<!-- uspc_subclass -->
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/uspc_subclass/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;uspc_subclasses</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>uspc_subclass_id</td>
        <td>ID for the subclass.</td>
        <td>string</td>
      </tr><tr>
        <td>uspc_subclass_title</td>
        <td>The description of the uspc subclass.</td>
        <td>text</td>
      </tr><tr>
        <td>uspc_mainclass</td>
        <td>The main U.S. classification (class) at issue.</td>
        <td>string</td>
      </tr><tr>
        <td>uspc_mainclass_id</td>
        <td>ID for the main U.S. classification at issue.</td>
        <td>string</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"uspc_subclass_id":"asc"&#125;]<br/>
  **Note:** USPC subclass values (`uspc_subclass_id`) typically contain the "/" character (for example, `427/286`). However, when using the single-id lookup endpoint `GET /api/v1/uspc_subclass/{uspc_subclass_id}/`, the URL parser does not accept "/".<br/> To use single-id lookup, substitute "/" with ":" (colon) in the value of `{uspc_subclass_id}`. For example: `427:286`. This requirement applies only to GET requests of the form `/api/v1/uspc_subclass/{uspc_subclass_id}/`.
  </div>
</details>

### WIPO

<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/wipo/
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;wipo</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>wipo_id</td>
        <td>Unique identifier for the WIPO entry.</td>
        <td>string</td>
      </tr><tr>
        <td>sector_title</td>
        <td>WIPO technology sector title.</td>
        <td>string</td>
      </tr><tr>
        <td>field_title</td>
        <td>WIPO technology field title.</td>
        <td>string</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"wipo_id":"asc"&#125;]<br/>
  </div>
</details>

## Patent Text Endpoints

### Brief Summary Text
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/g_brf_sum_text/&emsp;
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;g_brf_sum_texts</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>patent_id</td>
        <td>Patent Number.</td>
        <td>string</td>
      </tr><tr>
        <td>summary_text</td>
        <td>Brief summary text of the patent.</td>
        <td>text</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"patent_id":"asc"&#125;]<br/>
  </div>
</details>

### Claim
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/g_claim/&emsp;
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;g_claims</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>patent_id</td>
        <td>Patent Number.</td>
        <td>string</td>
      </tr><tr>
        <td>claim_sequence</td>
        <td>Order in which claims appear in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr><tr>
        <td>claim_number</td>
        <td>Contains claim number of claim formatted as 0-prefixed 5 digit number. This column will have a range of claims if those claims are cancelled.</td>
        <td>string</td>
      </tr><tr>
        <td>claim_text</td>
        <td>Claim Text.</td>
        <td>text</td>
      </tr><tr>
        <td>exemplary</td>
        <td>Whether the claim is one of the exemplary claims of the patent; 1 if exemplary claim, 0 otherwise.</td>
        <td>integer</td>
      </tr><tr>
        <td>claim_dependent</td>
        <td>Sequence number of claim this is dependent on. NULL if independent.</td>
        <td>string</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"patent_id":"asc"&#125;,&#123;"claim_sequence":"asc"&#125;]<br/>
  </div>
</details>

### Detail Description Text
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/g_detail_desc_text/&emsp;
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;g_detail_desc_texts</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>patent_id</td>
        <td>Patent Number.</td>
        <td>string</td>
      </tr><tr>
        <td>description_text</td>
        <td>Text of the description itself, excluding headings.</td>
        <td>text</td>
      </tr><tr>
        <td>description_length</td>
        <td>Length of the description text.</td>
        <td>integer</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"patent_id":"asc"&#125;]<br/>
  </div>
</details>

### Drawing Description Text
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/g_draw_desc_text/&emsp;
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;g_draw_desc_texts</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>patent_id</td>
        <td>Patent Number.</td>
        <td>string</td>
      </tr><tr>
        <td>draw_desc_sequence</td>
        <td>Order in which drawing descriptions appear in patent file, often the same as the figure id; values for this field begin at 1.</td>
        <td>integer</td>
      </tr><tr>
        <td>draw_desc_text</td>
        <td>Text of the description itself, excluding headings.</td>
        <td>text</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"patent_id":"asc"&#125;,&#123;"draw_desc_sequence":"asc"&#125;]<br/>
  </div>
</details>

## Publication Text Endpoints
### Brief Summary Text
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/pg_brf_sum_text/&emsp;
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;pg_brf_sum_texts</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>document_number</td>
        <td>Unique identifier for the publication document.</td>
        <td>string</td>
      </tr><tr>
        <td>summary_text</td>
        <td>Text of the brief summary itself, excluding headings.</td>
        <td>text</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"document_number":"asc"&#125;]<br/>
  </div>
</details>

### Claim
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/pg_claim/&emsp;
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;pg_claims</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>document_number</td>
        <td>Unique identifier for the Claim document.</td>
        <td>string</td>
      </tr><tr>
        <td>claim_sequence</td>
        <td>Order in which claims appear in patent file; values for this field begin at 0.</td>
        <td>integer</td>
      </tr><tr>
        <td>claim_text</td>
        <td>Claim Text.</td>
        <td>text</td>
      </tr><tr>
        <td>claim_dependent</td>
        <td>Sequence number of claim this is dependent on. NULL if independent.</td>
        <td>string</td>
      </tr><tr>
        <td>claim_number</td>
        <td>Contains claim number of claim formatted as 0-prefixed 5 digit number. This column will have a range of claims if those claims are cancelled.</td>
        <td>string</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"document_number":"asc"&#125;,&#123;"claim_sequence":"asc"&#125;]<br/>
  </div>
</details>

### Detail Description Text
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/pg_detail_desc_text/&emsp;
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;pg_detail_desc_texts</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>document_number</td>
        <td>Unique identifier for the publication document.</td>
        <td>string</td>
      </tr><tr>
        <td>description_text</td>
        <td>Detailed description text of the publication.</td>
        <td>text</td>
      </tr><tr>
        <td>description_length</td>
        <td>Length of the detailed description text.</td>
        <td>integer</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"document_number":"asc"&#125;]<br/>
  </div>
</details>

### Drawing Description Text
<details>
  <summary><small><b>GET / POST</b></small>&emsp;/api/v1/pg_draw_desc_text/&emsp;
    <span style={{float:'right'}}><i><td>response key:</td>&emsp;pg_draw_desc_texts</i></span></summary>
  <div>
    <table>
      <tr>
        <th>Field Name</th>
        <th>Description</th>
        <th>Data Type</th>
      </tr><tr>
        <td>document_number</td>
        <td>Unique identifier for the publication document.</td>
        <td>string</td>
      </tr><tr>
        <td>draw_desc_sequence</td>
        <td>Order in which drawing descriptions appear in patent file, often the same as the figure id; values for this field begin at 1.</td>
        <td>integer</td>
      </tr><tr>
        <td>draw_desc_text</td>
        <td>Text of the description itself, excluding headings.</td>
        <td>text</td>
      </tr>
    </table>
  <hr/>
  <b>default sort:</b>&emsp;[&#123;"document_number":"asc"&#125;,&#123;"draw_desc_sequence":"asc"&#125;]<br/>
  </div>
</details>
