---
sidebar_position: 2
---

# Search API Examples

The PatentsView Search API lets you explore the US patent system with flexible, powerful queries. This examples guide helps you construct API requests, explains common patterns, and shows how to interpret results.

**Note:** An API key is required for all requests. We have temporarily suspended new API Key grants. Please monitor this page for updates.

---

## Authentication

All requests require an API key in the header:

```text
X-Api-Key: YOUR_API_KEY_HERE
```

Responses are in JSON and always contain the `"error"` key. Successful responses also include:
- `"count"`: Number of records in the response.
- `"total_hits"`: Total records matching your query.
- An endpoint-specific key with the returned data (e.g., `"patents"`).

---

## Request Structure

You can use either GET or POST requests. The main parameters are:

| Parameter | Description              | Required |
|-----------|-------------------------|----------|
| `q`       | Query criteria (JSON)   | Yes      |
| `f`       | Fields to return (array)| No       |
| `s`       | Sort order (array)      | No       |
| `o`       | Options (pagination, etc.) | No   |

> **Note:** The maximum number of results returned for a single query is 1000, even if you request a larger size.

---


## Practical Examples

### Example 1: Basic Patent Query

This example shows how to retrieve the patent number, title, and date for specific patents. You provide a list of patent IDs, and the API returns details about each patent.

You can enter the following parameters to make the requests:

```json
{
  "f": ["patent_id", "patent_title", "patent_date"],
  "o": {"size": 200},
  "q": {"patent_id": ["D345393", "10905426", "11172927"]},
  "s": [{"patent_id": "asc"}]
}
```

**API Request URL Example**:

```https
https://search.patentsview.org/api/v1/patent/?q={"patent_id": ["D345393", "10905426", "11172927"]}&f=["patent_id","patent_title","patent_date"]
```

> **Note:** Replace YOUR_API_KEY_HERE with your personal PatentsView API key. We have temporarily suspended new API Key grants. Please monitor this page for updates.

**Python Example**:

```python
import requests
import json

url = "https://search.patentsview.org/api/v1/patent/"  # endpoint used here is "patent"
headers = {
    "X-Api-Key": "YOUR_API_KEY_HERE",  # Replace with your API key
    "accept": "application/json"
}
params = {
    "f": json.dumps(["patent_id", "patent_title", "patent_date"]),
    "o": json.dumps({"size": 200}),
    "q": json.dumps({"patent_id": ["D345393", "10905426", "11172927"]}),
    "s": json.dumps([{"patent_id": "asc"}])
}

response = requests.get(url, headers=headers, params=params)
print(response.json())
```
**Example Response**:

Here is a sample of what a successful response might look like:

```json
{
  "error": false,
  "count": 3,
  "total_hits": 3,
  "patents": [
    {
      "patent_id": "10905426",
      "patent_title": "Detachable motor powered surgical instrument",
      "patent_date": "2021-02-02"
    },
    {
      "patent_id": "11172927",
      "patent_title": "Staple cartridges for forming staples having differing formed staple heights",
      "patent_date": "2021-11-16"
    },
    {
      "patent_id": "D345393",
      "patent_title": "Turtle toy car",
      "patent_date": "1994-03-22"
    }
  ]
}
```

When you use an array as the value for a criterion in your query, the API will return results that match any of the values in that array. This is helpful when you want to search for multiple possible values for a field, such as several inventor names or patent IDs, in a single request.

### Example 2: Search Patents by Date Range

Retrieve patents granted between January 1, 2020 and December 31, 2020, returning the patent number, title, and date.

**API Endpoint:**
```https
https://search.patentsview.org/api/v1/patent/
```

**Request URL (GET with query parameters):**

```https
https://search.patentsview.org/api/v1/patent/?q={"_and":[{"_gte":{"patent_date":"2020-01-01"}},{"_lte":{"patent_date":"2020-12-31"}}]}&f=["patent_id","patent_title","patent_date"]&s=[{"patent_date":"desc"}]&o={"size":100}
```

**Query Breakdown:**
- `q`:
  - `_and`: Combine multiple conditions
  - `_gte`: `patent_date` more than or equal to 2020-01-01
  - `_lte`: `patent_date` less than or equal to 2020-12-31
- `f`: Return only `patent_id`, `patent_title`, `patent_date`
- `s`: Sort by `patent_date` descending
- `o`: Return up to 100 results

> **Note:**
> You must include your API key in the request headers for all API calls.
> Obtain an API key from the PatentsView Service Desk


**Swagger Example (POST request body):**
```json
{
  "q": {
    "_and": [
      {"_gte": {"patent_date": "2020-01-01"}},
      {"_lte": {"patent_date": "2020-12-31"}}
    ]
  },
  "f": ["patent_id", "patent_title", "patent_date"],
  "s": [{"patent_date": "desc"}],
  "o": {"size": 100}
}
```


**Python Example:**


```python
url = "https://search.patentsview.org/api/v1/patent/"
headers = {
    "X-Api-Key": "YOUR_API_KEY_HERE",  # Replace with your API key
    "accept": "application/json"
}
body = {
    "q": {
        "_and": [
            {"_gte": {"patent_date": "2020-01-01"}},
            {"_lte": {"patent_date": "2020-12-31"}}
        ]
    },
    "f": ["patent_id", "patent_title", "patent_date"],
    "s": [{"patent_date": "desc"}],
    "o": {"size": 100}
}

response = requests.post(url, headers=headers, json=body)
print(response.json())
```


### Example 3: Text Search in Patent Abstracts

Search for patents where the abstract contains both "machine" **and** "learning".

**API Endpoint:**
```https
https://search.patentsview.org/api/v1/patent/
```

**Swagger Example (POST request body):**
```json
{
  "q": {
    "_text_all": {"patent_abstract": "machine learning"}
  },
  "f": ["patent_id", "patent_title", "patent_abstract"],
  "o": {"size": 50}
}
```

**Query Breakdown:**
- `q`: Search for patents where the abstract contains both "machine" AND "learning"
- `f`: Return patent_id, patent_title, and patent_abstract
- `o`: Limit to 50 results

**Python Example:**

```python
url = "https://search.patentsview.org/api/v1/patent/"
headers = {
    "X-Api-Key": "YOUR_API_KEY_HERE",  # Replace with your API key
    "accept": "application/json"
}
body = {
    "q": {
        "_text_all": {"patent_abstract": "machine learning"}
    },
    "f": ["patent_id", "patent_title", "patent_abstract"],
    "o": {"size": 50}
}

response = requests.post(url, headers=headers, json=body)
print(response.json())
```


### Example 4: Complex Query with Multiple Conditions

Find patents from 2018 or later, that are either assigned to Google, Microsoft, or Amazon **or** invented by someone named Smith, and whose abstract mentions "artificial intelligence".

**API Endpoint:**

```https
https://search.patentsview.org/api/v1/patent/
```


**Example (GET request body):**
```json
{
  "q": {
    "_and": [
      {
        "_gte": {"patent_date": "2018-01-01"}
      },
      {
        "_or": [
          {"assignees.assignee_organization": {"_text_any": "Google Microsoft Amazon"}},
          {"inventors.inventor_last_name": "Smith"}
        ]
      },
      {
        "patent_abstract": {"_text_all": "artificial intelligence"}
      }
    ]
  },
  "f": [
    "patent_id",
    "patent_title",
    "patent_date",
    "patent_abstract",
    "assignees.assignee_organization",
    "inventors.inventor_last_name"
  ],
  "s": [{"patent_date": "desc"}],
  "o": {"size": 100}
}
```

**Query Breakdown:**
- `q`: Patents from 2018 or later (_gte operator);
  - AND (assigned to Google/Microsoft/Amazon OR invented by someone named Smith);
  - AND abstract mentions "artificial intelligence";
  - Returns relevant fields, sorted by date (descending), up to 100 results.

### Example 5: Using CPC Classification

Find patents in CPC section H, returning patent information and CPC classification details.

**API Endpoint:**
```https
https://search.patentsview.org/api/v1/patent/
```

**Swagger Example (POST request body):**
```json
{
  "q": {
    "cpc_current.cpc_section": "H"
  },
  "f": [
    "patent_id",
    "patent_title",
    "cpc_current.cpc_section",
    "cpc_current.cpc_subclass"
  ],
  "o": {"size": 50}
}
```


**Query Breakdown:**
- `q`: Find patents in CPC section H
- `f`: Return patent_id, patent_title, cpc_current.cpc_section, and cpc_current.cpc_subclass
- `o`: Limit to 50 results


### Example 6: Pagination Example

Demonstrate how to retrieve results page-by-page using the `size` and `after` options.

**API Endpoint:**

```https
https://search.patentsview.org/api/v1/patent/
```

**First Page Example (POST request body):**
```json
{
  "q": {"patent_type": "utility"},
  "f": ["patent_id", "patent_title", "patent_date"],
  "s": [
    {"patent_date": "desc"},
    {"patent_id": "asc"}
  ],
  "o": {"size": 100}
}
```

**Second Page Example (POST request body):**

```JSON
{
  "q": {"patent_type": "utility"},
  "f": ["patent_id", "patent_title", "patent_date"],
  "s": [
    {"patent_date": "desc"},
    {"patent_id": "asc"}
  ],
  "o": {"size": 100, "after": ["2024-10-01", "US12345678"]}
}
```

**Query Breakdown:**
- `q`: Filter for patents of type utility
- `f`: Return patent_id, patent_title, and patent_date
- `s`: Sort by patent_date descending, then by patent_id ascending
- `o`: "size": 100 — Number of results per page
  - "after": [...] — For pagination, supply the sort values of the last record from the previous page

---

## Quick Reference Card

### Request Template (POST)

```json
{
  "q": { /* Your query criteria */ },
  "f": [ /* Fields to return */ ],
  "s": [ /* Sort order */ ],
  "o": { /* Options */ }
}
```

Common Query Patterns

- Exact match
```json
{"field_name": "value"}
```

- Greater than / Less than

```json
{"_gt": {"field_name": "value"}}
{"_gte": {"field_name": "value"}}
{"_lt": {"field_name": "value"}}
{"_lte": {"field_name": "value"}}
```

- Text search

```json
{"field_name": {"_text_all": "search terms"}}
{"field_name": {"_text_any": "word1 word2"}}
{"field_name": {"_text_phrase": "exact phrase"}}
```


- AND condition

```json
{"_and": [{"field1": "value1"}, {"field2": "value2"}]}
```

- OR condition

```json
{"_or": [{"field1": "value1"}, {"field2": "value2"}]}
```

- Date range

```json
{
  "_and": [
    {"_gte": {"patent_date": "2020-01-01"}},
    {"_lte": {"patent_date": "2020-12-31"}}
  ]
}
```
