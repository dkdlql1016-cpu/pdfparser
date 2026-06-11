def assessment_tool_definitions(batch=False):
    submit_tool = {
        "name": "submit_verdict",
        "description": (
            "Submit whether the reviewer's requested change is present in the current report. "
            "Default to cleared or not_cleared; partial and unclear are narrow exceptions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["cleared", "partial", "unclear", "not_cleared"],
                    "description": (
                        "cleared: the requested change is present in the current report; "
                        "not_cleared: the requested change is absent; "
                        "partial: ONLY when the review explicitly asks for multiple separable changes and some but not all are made (never to express uncertainty); "
                        "unclear: ONLY when the review text itself has no actionable request (never because evidence is thin)"
                    ),
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "string"},
            },
            "required": ["verdict", "confidence", "reasoning"],
            "additionalProperties": False,
        },
    }
    if batch:
        submit_tool = {
            "name": "submit_verdicts",
            "description": (
                "Submit one verdict per review_id. Default to cleared (requested change present) "
                "or not_cleared (requested change absent). Use partial only when the review explicitly "
                "asks for multiple separable changes and some but not all are made; use unclear only when "
                "the review text itself has no actionable request."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "review_id": {"type": "string"},
                                "verdict": {"type": "string", "enum": ["cleared", "partial", "unclear", "not_cleared"]},
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                "reasoning": {"type": "string"},
                            },
                            "required": ["review_id", "verdict", "confidence", "reasoning"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        }
    return [
        {
            "name": "read_section",
            "description": "Read one markdown section from the previous or current report.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "enum": ["previous", "current"]},
                    "section_id": {"type": "string"},
                },
                "required": ["file", "section_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "search_markdown",
            "description": "Search a markdown report and return small context windows around matches.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "enum": ["previous", "current"]},
                    "query": {"type": "string"},
                },
                "required": ["file", "query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "keyword_search_markdown",
            "description": "Count keyword matches and return match positions in a markdown report.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "enum": ["previous", "current"]},
                    "keyword": {"type": "string"},
                    "case_sensitive": {"type": "boolean"},
                    "whole_word": {"type": "boolean"},
                    "max_hits": {"type": "integer", "minimum": 1, "maximum": 1000},
                },
                "required": ["file", "keyword"],
                "additionalProperties": False,
            },
        },
        submit_tool,
    ]


def change_assessment_tool_definitions(batch=False):
    verdict_description = (
        "Risk that the change makes the report fail accounting/auditing standards: "
        "high=clearly a significant compliance risk a reviewer would require fixing; "
        "medium=plausibly affects compliance and needs verification but not clearly a violation "
        "(uncertainty counts as medium, not low); "
        "low=no meaningful compliance risk (editorial, or consistent and well-supported)."
    )
    submit_tool = {
        "name": "submit_change_review",
        "description": (
            "Submit one change-risk assessment. Decide high/medium/low by whether the change "
            "creates a significant risk of failing accounting/auditing standards."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["high", "medium", "low"], "description": verdict_description},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "string"},
                "recommended_comment": {"type": "string"},
            },
            "required": ["verdict", "confidence", "reasoning", "recommended_comment"],
            "additionalProperties": False,
        },
    }
    if batch:
        submit_tool = {
            "name": "submit_change_reviews",
            "description": (
                "Submit one change-risk assessment per change_id. Decide high/medium/low by whether "
                "each change creates a significant risk of failing accounting/auditing standards."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "change_id": {"type": "integer"},
                                "verdict": {"type": "string", "enum": ["high", "medium", "low"], "description": verdict_description},
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                "reasoning": {"type": "string"},
                                "recommended_comment": {"type": "string"},
                            },
                            "required": ["change_id", "verdict", "confidence", "reasoning", "recommended_comment"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        }
    return [
        {
            "name": "read_section",
            "description": "Read one markdown section from the previous or current report.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "enum": ["previous", "current"]},
                    "section_id": {"type": "string"},
                },
                "required": ["file", "section_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "search_markdown",
            "description": "Search a markdown report and return small context windows around matches.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "enum": ["previous", "current"]},
                    "query": {"type": "string"},
                },
                "required": ["file", "query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "keyword_search_markdown",
            "description": "Count keyword matches and return match positions in a markdown report.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "enum": ["previous", "current"]},
                    "keyword": {"type": "string"},
                    "case_sensitive": {"type": "boolean"},
                    "whole_word": {"type": "boolean"},
                    "max_hits": {"type": "integer", "minimum": 1, "maximum": 1000},
                },
                "required": ["file", "keyword"],
                "additionalProperties": False,
            },
        },
        submit_tool,
    ]


def content_block_to_dict(block):
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    return block


def openai_tool_definitions(batch=False):
    tools = []
    for tool in assessment_tool_definitions(batch=batch):
        tools.append({
            "type": "function",
            "function": {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return tools


def openai_change_tool_definitions(batch=False):
    tools = []
    for tool in change_assessment_tool_definitions(batch=batch):
        tools.append({
            "type": "function",
            "function": {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return tools

