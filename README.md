# PatentsView PatentSearch API

## Dev Docs: Overview and Introduction

### Concepts

Developing for and maintaining search platform tools requires understanding the topics below:

- **Elasticsearch** — [Elastic fundamentals | Elastic Docs](https://www.elastic.co/guide/en/elasticsearch/reference/current/elasticsearch-intro.html)
- **Python/Django Framework** — [Django](https://www.djangoproject.com/)
- **Django REST Framework** — [Django REST framework](https://www.django-rest-framework.org/)
- **Redis** — <https://redis.io/>
- **MySQL** — [MySQL 8.0 Reference Manual — 1.2.1 What is MySQL?](https://dev.mysql.com/doc/refman/8.0/en/what-is-mysql.html)
- **Amazon Web Services (AWS) Basics** — [Why AWS?](https://aws.amazon.com/)
- **Git Basics** — [Git — What is Git?](https://git-scm.com/)
- **Docker Basics** — [What is Docker?](https://www.docker.com/what-docker/)
- **AWS Elastic Beanstalk** — [What is AWS Elastic Beanstalk?](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/Welcome.html)
- **AWS Elastic Container Service (ECS)** — [Amazon ECS](https://aws.amazon.com/ecs/)

---

### Software and Tools

#### Elasticsearch

- **Elasticsearch server** — [Download Elasticsearch](https://www.elastic.co/downloads/elasticsearch)
- Elasticsearch requires an installed **Java Development Kit (JDK)**. [OpenJDK](https://openjdk.org/) should work (e.g., via [Homebrew](https://formulae.brew.sh/formula/openjdk)).
- **Elasticvue** — [Elasticvue](https://elasticvue.com/)

#### Python Django Framework

- **Miniconda** — [Miniconda (Anaconda)](https://www.anaconda.com/docs/getting-started/miniconda/main)
- **PyCharm Professional** — [Download PyCharm (JetBrains)](https://www.jetbrains.com/pycharm/download/)
- **VS Code** (works in a pinch) — <https://code.visualstudio.com/Download>

#### Web Developer Tools

- **Insomnia** — [The Collaborative API Development Platform](https://insomnia.rest/)

#### Redis

- **Redis install (macOS)** — <https://redis.io/docs/latest/>
or **Redis downloads** — [Downloads Redis](https://redis.io/downloads/)
- **RedisInsight** — [Redis Insight (Free GUI & CLI Tool for Redis)](https://redis.io/insight/)

#### MySQL

- **MySQL Server** — [MySQL Community Downloads](https://dev.mysql.com/downloads/)
- **Sequel Ace** — [Sequel Ace](https://sequel-ace.com/)
- **DataGrip** (if you already have the license) — [DataGrip | JetBrains](https://www.jetbrains.com/datagrip/)
- **Windows option** — [MySQL Workbench](https://www.mysql.com/products/workbench/)

#### AWS

- **AWS Command Line Interface (CLI)** — [AWS CLI](https://aws.amazon.com/cli/)
- **AWS Elastic Beanstalk CLI (EB CLI)** — [AWS Elastic Beanstalk (EB CLI)](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/Welcome.html)

#### Docker

- **Docker Desktop** — [Docker Desktop: The #1 Containerization Tool for Developers](https://www.docker.com/products/docker-desktop/)

#### Node/Docusaurus

- **Node.js** — <https://nodejs.org/en/download>
- **Docusaurus** — [Installation | Docusaurus](https://docusaurus.io/docs/installation)

Node can also be installed via Homebrew on Mac. Docusaurus can then be installed by navigating to the `docs` folder inside your `PatentSearch-API` clone and running:

```bash
npm install
```

The required Node packages (including Docusaurus) will be detected from `package.json`.

---

### Setup / Introductory Tutorials

#### Elasticsearch

- **Elasticsearch local installation (quickstart)** — [Local development installation (quickstart) | Elastic Docs](https://www.elastic.co/docs/deploy-manage/deploy/self-managed/local-development-installation-quickstart)
- **Elasticvue usage** — <https://elasticvue.com/usage>

#### Python/Django Framework

- **Django** — [Getting started with Django](https://www.djangoproject.com/start/)
- **Django project setup**
  - [Create and run your first Django project | PyCharm](https://www.jetbrains.com/help/pycharm/creating-and-running-your-first-django-project.html#configuring-urls)
  - [Python and Django tutorial in Visual Studio Code](https://code.visualstudio.com/docs/python/tutorial-django)
- **Django REST Framework (DRF) Quickstart** — [Quickstart | Django REST framework](https://www.django-rest-framework.org/tutorial/quickstart/)
- **DRF In-depth Tutorial** — [1 - Serialization | Django REST framework](https://www.django-rest-framework.org/tutorial/1-serialization/)
- **Pytest** — [Get Started — pytest documentation](https://docs.pytest.org/en/7.4.x/getting-started.html)

#### Git/GitHub

- **Learn Git Branching** — [Learn Git Branching](https://learngitbranching.js.org/)
- **GitHub onboarding** — [Start your journey | GitHub Docs](https://docs.github.com/en/get-started/start-your-journey)

#### Docker

- **Docker 101 Tutorial** — [Docker 101 Tutorial | Docker](https://www.docker.com/101-tutorial/)

#### Elastic Beanstalk

- **Deploy Django to Elastic Beanstalk** — [Deploying a Django application to Elastic Beanstalk | AWS Elastic Beanstalk](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/create-deploy-python-django.html)

---

### Topic Tutorials

#### Elasticsearch

- **Elasticsearch Schema/Mapping Definition** — [Mapping | Elastic Docs](https://www.elastic.co/docs/manage-data/data-store/mapping)

---

## API Implementation Design

Django Rest Framework offers multiple levels of abstraction for serializer and view design. For serializers, these are (listed from more explicit to more abstract)

1. **Explicitly designed and declared `Serializer`**
2. [ModelSerializer](https://www.django-rest-framework.org/api-guide/serializers/#modelserializer)

Similarly, for views there are several levels of abstractions (listed from more explicit to more abstract)

1. Standalone (decorated) Python functions serving various requests (GET, POST, etc.)
    - [Tutorial 2: Requests and responses](https://www.django-rest-framework.org/tutorial/2-requests-and-responses/)
2. Class-based views where the class maps to a URL and methods in the class serve different requests
    - [Tutorial 3: Class-based views](https://www.django-rest-framework.org/tutorial/3-class-based-views/)
3. Class-based views with DRF Mixins
    - [Tutorial 3: Class-based views/Using-mixins](https://www.django-rest-framework.org/tutorial/3-class-based-views/#using-mixins)
4. Generic class-based views
    - [Tutorial 3: Class-based views/Using generic class-based views](https://www.django-rest-framework.org/tutorial/3-class-based-views/)

The PatentSearch API uses **Explicit serializer** and **Class-based views with custom mixins**.

---

### Variable Naming Agreement

There are multiple groups of elements in an API’s configuration that need to be aligned in non-obvious ways.
(All paths referenced originate at the root of the `PatentSearch-API` repo.)

#### Index Name

By convention this should match the response name where possible, but this is not a strict requirement.

- The name (or an alias) for the Elasticsearch index that stores the data for the endpoint
- The string value assigned to `self.index` in the `__init__` method of the `{entity}Endpoint` class in:
- `API/endpoints/{entity}_endpoint_configuration.py`
- The final named argument of the `__init__` method of the `{entity}ResponseDocument` class in:
- `API/endpoints/{entity}_endpoint_configuration.py`

#### Response Name

By convention this is the plural form of the endpoint path, but this is not a strict requirement.

- The name of the object attribute assigned in the `__init__` method of the `{entity}ResponseDocument` class in:
    - `API/endpoints/{entity}_endpoint_configuration.py`
- The first key under `properties` under `{entity}SuccessResponse` in:
    - `API/static/openapi.json`  
- The variable name assigned to the `{entity}Serializer` object in the `APISerializer` class in:
    - `API/serializers/APISerializer.py`

#### Endpoint Path

By convention this is the word for a single entity within the endpoint/index (e.g., `inventor`).

- The string used as the first argument of the `path` or `re_path` function call within the `urlpatterns` list in:
  - `API/urls.py`
- The last portion of the path used as a key for that endpoint under `paths` in:
  - `API/static/openapi.json`
appended to the standard path prefix:
  - `/api/v1/`, `/api/v1/patent/`, or `/api/v1/publication/` (corresponds to the Swagger page)

#### Field Names

- The variable names in the class `{entity}Serializer` in:
  - `API/endpoints/{entity}_endpoint_configuration.py`
- The keys within:
  - `components` → `schemas` → `{entity}SuccessResponse` → `properties` → `{ResponseName}` → `items` → `properties` in `API/static/openapi.json`
- The **GET** defaults located at:
  - `paths` → `/api/v1/{EndpointPath}` → `get` → `parameters` → `schema` → `default` in `API/static/openapi.json` (both for the `f` and `s` parameters)
- The **POST** defaults located at:
  - `components` → `schemas` → `{entity}PostRequestBody` → `properties` → `f` / `s` → `default` in `API/static/openapi.json` (both for the `f` and `s` parameters)
- The field names in the Elasticsearch index that stores the data for the endpoint

---

## Endpoint Creation Tutorial

This tutorial walks an onboarding developer step-by-step through creating an endpoint in the PatentSearch-API.

### Setup

- Ensure local dependencies are available (MySQL, Elasticsearch, Redis as needed, and project dependencies installed).
- Identify the **source table(s)** and the **target Elasticsearch index** for the new endpoint.

---

### Creating Data Source

1. Connect to your local **MySQL** instance.
2. Create (or identify) a database and table(s) that will act as the source for the endpoint.
3. Verify the table(s) contain the expected columns and sample data.

---

### Creating Elasticsearch Schema

1. Create an Elasticsearch index schema file (**JSON**) with fields for all columns required by the endpoint. *(See Elasticsearch mapping tutorials.)*
2. Create a data loading project/folder in PyCharm/VS Code.
3. Install the `es-data-load` package.
4. Using `es-data-load`, create the target index in your local Elasticsearch instance.
5. Connect using **Elasticvue** and verify the index exists and has the expected mapping.

---

### Loading Data

1. Create a **MySQL → Elasticsearch mapping file**.
2. Run the data load using the `es-data-load` package.
3. Verify documents exist in the index (via Elasticvue or Elasticsearch queries).

---

### Creating API Endpoint

#### Creating Serializer

Create a serializer class that inherits from `serializers.Serializer`:

- DRF tutorial: [Tutorial 1 - Serialization](https://www.django-rest-framework.org/tutorial/1-serialization/)

Example:

```python
from rest_framework import serializers

class {entity_name}Serializer(serializers.Serializer):
    # Declare the fields included in the endpoint, e.g.:
    # id = serializers.CharField()
    # name = serializers.CharField()
    pass
```

---

#### Creating Endpoint Configuration

Create a generic class to define **endpoint configuration** and **response document** structure.

> Replace `{entity_name}` with the endpoint/entity name (e.g., `patent`, `assignee`, `inventor`, etc.) as it applies.

```python
# Defines the wrapper for a list of response documents
class {entity_name}ResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, {entity_name}):
        super().__init__(error, count, total_hits)
        self.{entity_name} = {entity_name}


# Fields, operators, Elasticsearch, and other configuration for the endpoint
class {entity_name}Endpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "{entity_name}"

        # Fields which are configured as "text" type in ES and consequently require
        # ".keyword" suffix for keyword-like operations
        self.keyword_field_translations = []
  
        # Default f and s fields
        self.f = ["id"]
        self.s = [{"id": "asc"}]

        # List of all allowed fields
        self.field_list = list({entity_name}Serializer.__dict__["_declared_fields"].keys())

        # Wrapper that defines the format for API response
        self.response_encoder = {entity_name}ResponseDocument
``` 

---

#### Creating DRF View Classes (List + Detail)

Create DRF view classes (one for list view and one for detail view):

```python
# DRF View for showing multiple entities
class {entity_name}List(PVAPIListView, {entity_name}Endpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        {entity_name}Endpoint.__init__(self)


# DRF View for showing single entity
class {entity_name}Detail(PVAPIDetailView, {entity_name}Endpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        {entity_name}Endpoint.__init__(self)
        self.pk_field = "id"
``` 
  
---

#### Add URL Patterns (`urls.py`)

Add URL patterns to `API/urls.py`:

```python
from django.urls import path, re_path

urlpatterns = [
    path("{entity_name}/<str:pk>/", {entity_name}Detail.as_view(), name="{entity_name}-detail"),
    re_path(r"^{entity_name}/?$", {entity_name}List.as_view(), name="{entity_name}-list"),
]
```