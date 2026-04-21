from API.exceptions import InvalidFieldException


class QueryParser:
    def __init__(self):
        self.clauses = {
            "_eq": {
                "parent": False,
                "type": "equality",
                "clause": "match",
                "operator": None,
                "plural_clause": "terms",
            },
            "_neq": {
                "translate": "_eq",
                "conjunction": "_not",
            },
            "_gte": {
                "parent": True,
                "type": "range",
                "operator": "gte",
                "clause": "range",
            },
            "_gt": {
                "parent": True,
                "type": "range",
                "operator": "gt",
                "clause": "range",
            },
            "_lt": {
                "parent": True,
                "type": "range",
                "operator": "lt",
                "clause": "range",
            },
            "_lte": {
                "parent": True,
                "type": "range",
                "operator": "lte",
                "clause": "range",
            },
            "_contains": {
                "parent": True,
                "clause": "wildcard",
                "operator": "value",
                "type": "string",
                "transformation": "*{v}*",
                "plural_conjunction": "_or",
                "additional_settings": {"case_insensitive": True},
            },
            "_begins": {
                "parent": True,
                "type": "string",
                "operator": "value",
                "clause": "prefix",
                "plural_conjunction": "_or",
                "additional_settings": {"case_insensitive": True},
            },
            "_text_all": {
                "parent": False,
                "type": "fulltext",
                "operator": "query",
                "clause": "match",
                "additional_settings": {"operator": "and"},
            },
            "_text_any": {
                "parent": True,
                "type": "fulltext",
                "operator": "query",
                "clause": "match",
                "additional_settings": {"operator": "or"},
            },
            "_text_phrase": {
                "parent": False,
                "type": "fulltext",
                "operator": "query",
                "clause": "match_phrase",
            },
            "_not": {
                "parent": False,
                "type": "conjunction",
                "clause": "bool",
                "operator": "must_not",
            },
            "_and": {
                "parent": False,
                "type": "conjunction",
                "clause": "bool",
                "operator": "filter",
            },
            "_or": {
                "parent": False,
                "type": "conjunction",
                "clause": "bool",
                "operator": "should",
            },
        }
        self.involved_fields = {}

    def generate_search_query_component(
        self, field, value, operator_setting, keyword_suffix_fields, pad_patent_id=False
    ):
        clause = operator_setting[
            "clause"
        ]  # Elasticsearch keywords translated by above dictionary
        if isinstance(value, list):  # how to deal with lists at innermost level
            if "plural_conjunction" in operator_setting:
                # chain the items by recursing within a conjunction operator
                # testing for "contains"
                conjunction_setting = self.clauses[
                    operator_setting["plural_conjunction"]
                ]
                component = {
                    conjunction_setting["clause"]: {
                        conjunction_setting["operator"]: [
                            self.generate_search_query_component(
                                field, v, operator_setting, keyword_suffix_fields
                            )
                            for v in value
                        ]
                    }
                }
                return component

            elif "plural_clause" in operator_setting:
                # use a different operator for lists
                # currenty only used by _eq
                clause = operator_setting["plural_clause"]

            # if "plural_clause" used or if no splural-specific setting
            inner_component = value

        else:  # value is not a list
            operator = operator_setting["operator"]
            if "transformation" in operator_setting:
                value = operator_setting["transformation"].format(v=value)
            if operator:  # all cases except "_eq"
                inner_component = {operator: value}
                additional_settings = operator_setting.get("additional_settings", {})
                inner_component.update(additional_settings)
            else:  # only used for "_eq"
                inner_component = value
        # Add .keyword suffix for string fields stored as text type if the comparison is equals, contains, or begins
        if field in keyword_suffix_fields and operator_setting["type"] in (
            "equality",
            "string",
        ):
            field = f"{field}.keyword"
        if field == "patent_id" and pad_patent_id:
            field = "patent_zero_prefix"
        self.involved_fields[field] = 1
        component = {clause: {field: inner_component}}
        return component

    def generate_search_query(
        self,
        query_component,
        nested_paths,
        allowed_fields,
        keyword_suffix_fields,
        pad_patent_id=False,
        level=0,
    ):
        # `allowed_fields` contains just the top-level fields, not the nested sub-fields
        # note `keyword_suffix_fields` is passed from the endpoint class as `self.keyword_field_translations`, which contains the list of nested fields
        parent = True  # represents an operator is the key at this level
        operator, value = extract_key_value(query_component)
        operator_setting = self.clauses.get(
            operator, self.clauses.get("_eq")
        )  # get the settings for this operator, using the equality setting as default.
        if operator not in self.clauses:
            parent = False  # indicates end of operator chain; `operator` should be a field name now.

        if "translate" in operator_setting:  # currently only used by "_neq" (unequal)
            target_operator_setting = self.clauses.get(operator_setting["conjunction"])
            new_operator = operator_setting["translate"]
            operator_setting = target_operator_setting
            value = [value]
            operator = new_operator

        if operator_setting["type"] == "conjunction":
            components = []
            if operator == "_not":
                value = [value]
            for q_term in value:
                components.append(
                    self.generate_search_query(
                        q_term,
                        nested_paths,
                        allowed_fields,
                        keyword_suffix_fields,
                        pad_patent_id,
                        level=level + 1,
                    )
                )
            component = {
                operator_setting["clause"]: {operator_setting["operator"]: components}
            }

        else:  # operator_setting['type'] != 'conjunction'
            if parent:  # operator active
                field, value = extract_key_value(value)
            else:  # non-operators (fields)
                field = operator
                operator = ""  # remaining operation depends on operator_setting, which matches the operator or defaults to _eq
            if field not in allowed_fields and not any(
                [field.startswith(f"{x}.") for x in allowed_fields]
            ):
                raise InvalidFieldException("Invalid field: {f}".format(f=field))
            current_nested_path = None
            if (
                "." in field
            ):  # nested field condition - some entity name translation needed, e.g. patent.cpc_at_issue -> patent.cpcs_at_issue
                nested_document, nested_field = field.split(".", 1)
                if nested_document not in nested_paths:
                    raise InvalidFieldException(
                        f"Invalid field: {field}. {nested_document} is not a nested field"
                    )
                current_nested_path = nested_paths[nested_document]
                field = f"{current_nested_path}.{nested_field}"
            component = self.generate_search_query_component(
                field, value, operator_setting, keyword_suffix_fields, pad_patent_id
            )
            if current_nested_path is not None:
                component = {
                    "nested": {"path": current_nested_path, "query": component}
                }
        return component

    def get_involved_fields(self):
        return self.involved_fields


def extract_key_value(single_key_dict):
    key = list(single_key_dict.keys())[0]
    value = single_key_dict[key]
    return key, value
