def assessment_tool_definitions(batch=False):
    submit_tool = {
        "name": "submit_verdict",
        "description": "Submit the structured assessment verdict.",
        "input_schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["cleared", "partial", "unclear", "not_cleared"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "string"},
                "evidence_old": {"type": "string"},
                "evidence_new": {"type": "string"},
            },
            "required": ["verdict", "confidence", "reasoning", "evidence_old", "evidence_new"],
            "additionalProperties": False,
        },
    }
    if batch:
        submit_tool = {
            "name": "submit_verdicts",
            "description": "Submit one structured assessment verdict for each review_id in the batch.",
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
                                "evidence_old": {"type": "string"},
                                "evidence_new": {"type": "string"},
                            },
                            "required": ["review_id", "verdict", "confidence", "reasoning", "evidence_old", "evidence_new"],
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
    submit_tool = {
        "name": "submit_change_review",
        "description": "Submit one structured change-risk assessment and recommended review comment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["high", "medium", "low"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "string"},
                "recommended_comment": {"type": "string"},
                "evidence_old": {"type": "string"},
                "evidence_new": {"type": "string"},
            },
            "required": ["verdict", "confidence", "reasoning", "recommended_comment", "evidence_old", "evidence_new"],
            "additionalProperties": False,
        },
    }
    if batch:
        submit_tool = {
            "name": "submit_change_reviews",
            "description": "Submit one structured change-risk assessment for each change_id in the batch.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "change_id": {"type": "integer"},
                                "verdict": {"type": "string", "enum": ["high", "medium", "low"]},
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                "reasoning": {"type": "string"},
                                "recommended_comment": {"type": "string"},
                                "evidence_old": {"type": "string"},
                                "evidence_new": {"type": "string"},
                            },
                            "required": ["change_id", "verdict", "confidence", "reasoning", "recommended_comment", "evidence_old", "evidence_new"],
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

