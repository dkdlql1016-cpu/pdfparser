import json
import os

from ai_config_utils import ai_provider_for_model
from ai_runtime_utils import (
    assessment_tool_definitions,
    change_assessment_tool_definitions,
    content_block_to_dict,
    openai_change_tool_definitions,
    openai_tool_definitions,
)
from assessment_normalizers import (
    normalize_assessment_payload,
    normalize_batch_assessment_payload,
    normalize_change_assessment_payload,
    normalize_change_batch_assessment_payload,
    openai_tool_call_args,
)
from assessment_prompt_builders import build_batch_assessment_prompt, build_change_batch_assessment_prompt
from section_context_utils import search_markdown_context, section_context_from_markdown


def call_ai_assessment_openai(prompt, tool_context, *, model, prompt_path, prompt_fallback, load_system_prompt_fn):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError("openai package is not installed") from e
    client = OpenAI(api_key=api_key)
    messages = [
        {"role": "system", "content": load_system_prompt_fn(prompt_path, prompt_fallback)},
        {"role": "user", "content": prompt},
    ]
    tool_trace = []
    for _ in range(8):
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            tools=openai_tool_definitions(batch=False),
            messages=messages,
        )
        choice = response.choices[0].message
        tool_calls = list(choice.tool_calls or [])
        assistant_message = {"role": "assistant", "content": choice.content or ""}
        if tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in tool_calls
            ]
        messages.append(assistant_message)
        if not tool_calls:
            messages.append({"role": "user", "content": "Use read_section or search_markdown, then call submit_verdict."})
            continue
        for call in tool_calls:
            name = call.function.name
            args = openai_tool_call_args(call.function.arguments)
            if name == "submit_verdict":
                payload = normalize_assessment_payload(args)
                payload["tool_trace"] = tool_trace
                return payload
            if name == "read_section":
                file_key = args.get("file")
                section_id = args.get("section_id")
                md_path, section_map = tool_context[file_key]
                result = {"file": file_key, "section_id": section_id, "text": section_context_from_markdown(md_path, section_map, section_id)}
                tool_trace.append({"tool": name, "file": file_key, "section_id": section_id})
            elif name == "search_markdown":
                file_key = args.get("file")
                query = args.get("query", "")
                md_path, _section_map = tool_context[file_key]
                result = {"file": file_key, "query": query, "hits": search_markdown_context(md_path, query)}
                tool_trace.append({"tool": name, "file": file_key, "query": query})
            else:
                result = {"error": f"Unknown tool: {name}"}
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)})
    raise ValueError("AI assessment reached the tool-call limit")


def call_ai_assessment_batch_openai(context_packs, available_sections, tool_context, *, model, prompt_path, prompt_fallback, load_system_prompt_fn):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError("openai package is not installed") from e
    client = OpenAI(api_key=api_key)
    messages = [
        {"role": "system", "content": load_system_prompt_fn(prompt_path, prompt_fallback)},
        {"role": "user", "content": build_batch_assessment_prompt(context_packs, available_sections)},
    ]
    tool_trace = []
    for _ in range(8):
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            tools=openai_tool_definitions(batch=True),
            messages=messages,
        )
        choice = response.choices[0].message
        tool_calls = list(choice.tool_calls or [])
        assistant_message = {"role": "assistant", "content": choice.content or ""}
        if tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in tool_calls
            ]
        messages.append(assistant_message)
        if not tool_calls:
            messages.append({"role": "user", "content": "Use optional tools only if needed, then call submit_verdicts with one item per review_id."})
            continue
        for call in tool_calls:
            name = call.function.name
            args = openai_tool_call_args(call.function.arguments)
            if name == "submit_verdicts":
                payloads = normalize_batch_assessment_payload(args)
                for payload in payloads.values():
                    payload["tool_trace"] = tool_trace
                return payloads
            if name == "read_section":
                file_key = args.get("file")
                section_id = args.get("section_id")
                md_path, section_map = tool_context[file_key]
                result = {"file": file_key, "section_id": section_id, "text": section_context_from_markdown(md_path, section_map, section_id)}
                tool_trace.append({"tool": name, "file": file_key, "section_id": section_id})
            elif name == "search_markdown":
                file_key = args.get("file")
                query = args.get("query", "")
                md_path, _section_map = tool_context[file_key]
                result = {"file": file_key, "query": query, "hits": search_markdown_context(md_path, query)}
                tool_trace.append({"tool": name, "file": file_key, "query": query})
            else:
                result = {"error": f"Unknown tool: {name}"}
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)})
    raise ValueError("AI batch assessment reached the tool-call limit")


def call_ai_assessment(prompt, tool_context, *, model, prompt_path, prompt_fallback, load_system_prompt_fn):
    if ai_provider_for_model(model) == "openai":
        return call_ai_assessment_openai(
            prompt,
            tool_context,
            model=model,
            prompt_path=prompt_path,
            prompt_fallback=prompt_fallback,
            load_system_prompt_fn=load_system_prompt_fn,
        )
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    try:
        from anthropic import Anthropic
    except Exception as e:
        raise RuntimeError("anthropic package is not installed") from e
    client = Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": prompt}]
    system_prompt = load_system_prompt_fn(prompt_path, prompt_fallback)
    tool_trace = []
    for _ in range(8):
        msg = client.messages.create(
            model=model,
            max_tokens=1200,
            temperature=0,
            system=system_prompt,
            tools=assessment_tool_definitions(),
            messages=messages,
        )
        messages.append({"role": "assistant", "content": [content_block_to_dict(block) for block in msg.content]})
        tool_results = []
        for block in msg.content:
            if getattr(block, "type", "") != "tool_use":
                continue
            name = getattr(block, "name", "")
            args = getattr(block, "input", {}) or {}
            if name == "submit_verdict":
                payload = normalize_assessment_payload(args)
                payload["tool_trace"] = tool_trace
                return payload
            if name == "read_section":
                file_key = args.get("file")
                section_id = args.get("section_id")
                md_path, section_map = tool_context[file_key]
                result = {"file": file_key, "section_id": section_id, "text": section_context_from_markdown(md_path, section_map, section_id)}
                tool_trace.append({"tool": name, "file": file_key, "section_id": section_id})
            elif name == "search_markdown":
                file_key = args.get("file")
                query = args.get("query", "")
                md_path, _section_map = tool_context[file_key]
                result = {"file": file_key, "query": query, "hits": search_markdown_context(md_path, query)}
                tool_trace.append({"tool": name, "file": file_key, "query": query})
            else:
                result = {"error": f"Unknown tool: {name}"}
            tool_results.append({"type": "tool_result", "tool_use_id": getattr(block, "id"), "content": json.dumps(result, ensure_ascii=False)})
        if not tool_results:
            messages.append({"role": "user", "content": "Use read_section or search_markdown, then call submit_verdict."})
        else:
            messages.append({"role": "user", "content": tool_results})
    raise ValueError("AI assessment reached the tool-call limit")


def call_ai_assessment_batch(context_packs, available_sections, tool_context, *, model, prompt_path, prompt_fallback, load_system_prompt_fn):
    if ai_provider_for_model(model) == "openai":
        return call_ai_assessment_batch_openai(
            context_packs,
            available_sections,
            tool_context,
            model=model,
            prompt_path=prompt_path,
            prompt_fallback=prompt_fallback,
            load_system_prompt_fn=load_system_prompt_fn,
        )
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    try:
        from anthropic import Anthropic
    except Exception as e:
        raise RuntimeError("anthropic package is not installed") from e
    client = Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": build_batch_assessment_prompt(context_packs, available_sections)}]
    system_prompt = load_system_prompt_fn(prompt_path, prompt_fallback)
    tool_trace = []
    for _ in range(8):
        msg = client.messages.create(
            model=model,
            max_tokens=3000,
            temperature=0,
            system=system_prompt,
            tools=assessment_tool_definitions(batch=True),
            messages=messages,
        )
        messages.append({"role": "assistant", "content": [content_block_to_dict(block) for block in msg.content]})
        tool_results = []
        for block in msg.content:
            if getattr(block, "type", "") != "tool_use":
                continue
            name = getattr(block, "name", "")
            args = getattr(block, "input", {}) or {}
            if name == "submit_verdicts":
                payloads = normalize_batch_assessment_payload(args)
                for payload in payloads.values():
                    payload["tool_trace"] = tool_trace
                return payloads
            if name == "read_section":
                file_key = args.get("file")
                section_id = args.get("section_id")
                md_path, section_map = tool_context[file_key]
                result = {"file": file_key, "section_id": section_id, "text": section_context_from_markdown(md_path, section_map, section_id)}
                tool_trace.append({"tool": name, "file": file_key, "section_id": section_id})
            elif name == "search_markdown":
                file_key = args.get("file")
                query = args.get("query", "")
                md_path, _section_map = tool_context[file_key]
                result = {"file": file_key, "query": query, "hits": search_markdown_context(md_path, query)}
                tool_trace.append({"tool": name, "file": file_key, "query": query})
            else:
                result = {"error": f"Unknown tool: {name}"}
            tool_results.append({"type": "tool_result", "tool_use_id": getattr(block, "id"), "content": json.dumps(result, ensure_ascii=False)})
        if not tool_results:
            messages.append({"role": "user", "content": "Use optional tools only if needed, then call submit_verdicts with one item per review_id."})
        else:
            messages.append({"role": "user", "content": tool_results})
    raise ValueError("AI batch assessment reached the tool-call limit")


def call_ai_change_assessment_openai(prompt, tool_context, *, model, prompt_path, prompt_fallback, load_system_prompt_fn):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError("openai package is not installed") from e
    client = OpenAI(api_key=api_key)
    messages = [
        {"role": "system", "content": load_system_prompt_fn(prompt_path, prompt_fallback)},
        {"role": "user", "content": prompt},
    ]
    tool_trace = []
    for _ in range(8):
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            tools=openai_change_tool_definitions(batch=False),
            messages=messages,
        )
        choice = response.choices[0].message
        tool_calls = list(choice.tool_calls or [])
        assistant_message = {"role": "assistant", "content": choice.content or ""}
        if tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in tool_calls
            ]
        messages.append(assistant_message)
        if not tool_calls:
            messages.append({"role": "user", "content": "Use optional tools only if needed, then call submit_change_review."})
            continue
        for call in tool_calls:
            name = call.function.name
            args = openai_tool_call_args(call.function.arguments)
            if name == "submit_change_review":
                payload = normalize_change_assessment_payload(args)
                payload["tool_trace"] = tool_trace
                return payload
            if name == "read_section":
                file_key = args.get("file")
                section_id = args.get("section_id")
                md_path, section_map = tool_context[file_key]
                result = {"file": file_key, "section_id": section_id, "text": section_context_from_markdown(md_path, section_map, section_id)}
                tool_trace.append({"tool": name, "file": file_key, "section_id": section_id})
            elif name == "search_markdown":
                file_key = args.get("file")
                query = args.get("query", "")
                md_path, _section_map = tool_context[file_key]
                result = {"file": file_key, "query": query, "hits": search_markdown_context(md_path, query)}
                tool_trace.append({"tool": name, "file": file_key, "query": query})
            else:
                result = {"error": f"Unknown tool: {name}"}
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)})
    raise ValueError("AI change assessment reached the tool-call limit")


def call_ai_change_assessment_batch_openai(context_packs, available_sections, tool_context, *, model, prompt_path, prompt_fallback, load_system_prompt_fn):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError("openai package is not installed") from e
    client = OpenAI(api_key=api_key)
    messages = [
        {"role": "system", "content": load_system_prompt_fn(prompt_path, prompt_fallback)},
        {"role": "user", "content": build_change_batch_assessment_prompt(context_packs, available_sections)},
    ]
    tool_trace = []
    for _ in range(8):
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            tools=openai_change_tool_definitions(batch=True),
            messages=messages,
        )
        choice = response.choices[0].message
        tool_calls = list(choice.tool_calls or [])
        assistant_message = {"role": "assistant", "content": choice.content or ""}
        if tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in tool_calls
            ]
        messages.append(assistant_message)
        if not tool_calls:
            messages.append({"role": "user", "content": "Use optional tools only if needed, then call submit_change_reviews."})
            continue
        for call in tool_calls:
            name = call.function.name
            args = openai_tool_call_args(call.function.arguments)
            if name == "submit_change_reviews":
                payloads = normalize_change_batch_assessment_payload(args)
                for payload in payloads.values():
                    payload["tool_trace"] = tool_trace
                return payloads
            if name == "read_section":
                file_key = args.get("file")
                section_id = args.get("section_id")
                md_path, section_map = tool_context[file_key]
                result = {"file": file_key, "section_id": section_id, "text": section_context_from_markdown(md_path, section_map, section_id)}
                tool_trace.append({"tool": name, "file": file_key, "section_id": section_id})
            elif name == "search_markdown":
                file_key = args.get("file")
                query = args.get("query", "")
                md_path, _section_map = tool_context[file_key]
                result = {"file": file_key, "query": query, "hits": search_markdown_context(md_path, query)}
                tool_trace.append({"tool": name, "file": file_key, "query": query})
            else:
                result = {"error": f"Unknown tool: {name}"}
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)})
    raise ValueError("AI change batch assessment reached the tool-call limit")


def call_ai_change_assessment(prompt, tool_context, *, model, prompt_path, prompt_fallback, load_system_prompt_fn):
    if ai_provider_for_model(model) == "openai":
        return call_ai_change_assessment_openai(
            prompt,
            tool_context,
            model=model,
            prompt_path=prompt_path,
            prompt_fallback=prompt_fallback,
            load_system_prompt_fn=load_system_prompt_fn,
        )
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    try:
        from anthropic import Anthropic
    except Exception as e:
        raise RuntimeError("anthropic package is not installed") from e
    client = Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": prompt}]
    system_prompt = load_system_prompt_fn(prompt_path, prompt_fallback)
    tool_trace = []
    for _ in range(8):
        msg = client.messages.create(
            model=model,
            max_tokens=1400,
            temperature=0,
            system=system_prompt,
            tools=change_assessment_tool_definitions(),
            messages=messages,
        )
        messages.append({"role": "assistant", "content": [content_block_to_dict(block) for block in msg.content]})
        tool_results = []
        for block in msg.content:
            if getattr(block, "type", "") != "tool_use":
                continue
            name = getattr(block, "name", "")
            args = getattr(block, "input", {}) or {}
            if name == "submit_change_review":
                payload = normalize_change_assessment_payload(args)
                payload["tool_trace"] = tool_trace
                return payload
            if name == "read_section":
                file_key = args.get("file")
                section_id = args.get("section_id")
                md_path, section_map = tool_context[file_key]
                result = {"file": file_key, "section_id": section_id, "text": section_context_from_markdown(md_path, section_map, section_id)}
                tool_trace.append({"tool": name, "file": file_key, "section_id": section_id})
            elif name == "search_markdown":
                file_key = args.get("file")
                query = args.get("query", "")
                md_path, _section_map = tool_context[file_key]
                result = {"file": file_key, "query": query, "hits": search_markdown_context(md_path, query)}
                tool_trace.append({"tool": name, "file": file_key, "query": query})
            else:
                result = {"error": f"Unknown tool: {name}"}
            tool_results.append({"type": "tool_result", "tool_use_id": getattr(block, "id"), "content": json.dumps(result, ensure_ascii=False)})
        if not tool_results:
            messages.append({"role": "user", "content": "Use optional tools only if needed, then call submit_change_review."})
        else:
            messages.append({"role": "user", "content": tool_results})
    raise ValueError("AI change assessment reached the tool-call limit")


def call_ai_change_assessment_batch(context_packs, available_sections, tool_context, *, model, prompt_path, prompt_fallback, load_system_prompt_fn):
    if ai_provider_for_model(model) == "openai":
        return call_ai_change_assessment_batch_openai(
            context_packs,
            available_sections,
            tool_context,
            model=model,
            prompt_path=prompt_path,
            prompt_fallback=prompt_fallback,
            load_system_prompt_fn=load_system_prompt_fn,
        )
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    try:
        from anthropic import Anthropic
    except Exception as e:
        raise RuntimeError("anthropic package is not installed") from e
    client = Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": build_change_batch_assessment_prompt(context_packs, available_sections)}]
    system_prompt = load_system_prompt_fn(prompt_path, prompt_fallback)
    tool_trace = []
    for _ in range(8):
        msg = client.messages.create(
            model=model,
            max_tokens=3000,
            temperature=0,
            system=system_prompt,
            tools=change_assessment_tool_definitions(batch=True),
            messages=messages,
        )
        messages.append({"role": "assistant", "content": [content_block_to_dict(block) for block in msg.content]})
        tool_results = []
        for block in msg.content:
            if getattr(block, "type", "") != "tool_use":
                continue
            name = getattr(block, "name", "")
            args = getattr(block, "input", {}) or {}
            if name == "submit_change_reviews":
                payloads = normalize_change_batch_assessment_payload(args)
                for payload in payloads.values():
                    payload["tool_trace"] = tool_trace
                return payloads
            if name == "read_section":
                file_key = args.get("file")
                section_id = args.get("section_id")
                md_path, section_map = tool_context[file_key]
                result = {"file": file_key, "section_id": section_id, "text": section_context_from_markdown(md_path, section_map, section_id)}
                tool_trace.append({"tool": name, "file": file_key, "section_id": section_id})
            elif name == "search_markdown":
                file_key = args.get("file")
                query = args.get("query", "")
                md_path, _section_map = tool_context[file_key]
                result = {"file": file_key, "query": query, "hits": search_markdown_context(md_path, query)}
                tool_trace.append({"tool": name, "file": file_key, "query": query})
            else:
                result = {"error": f"Unknown tool: {name}"}
            tool_results.append({"type": "tool_result", "tool_use_id": getattr(block, "id"), "content": json.dumps(result, ensure_ascii=False)})
        if not tool_results:
            messages.append({"role": "user", "content": "Use optional tools only if needed, then call submit_change_reviews."})
        else:
            messages.append({"role": "user", "content": tool_results})
    raise ValueError("AI change batch assessment reached the tool-call limit")

