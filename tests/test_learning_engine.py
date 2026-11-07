"""Synthetic bounded-loop checks, not real model or citation quality results."""
import pytest
from pydantic import ValidationError
from project_evidence.agent_protocol import (ModelContext, ScriptedAdapter, OUTLINE_GOAL,
    SECTION_GOAL, OUTLINE_INSTRUCTION, SYSTEM_INSTRUCTION)
from test_agent_engine import run, tool_turn, CONTEXT, directory_turn
from test_learning_core import outline, explanation


def anchor(section=False):
    value = {"directory_task_id": "directory-1", "chapter_index": 0, "title": "查询入口",
             "summary": "待核对查询入口", "entry_hints": ["src/Example.java"]}
    if section:
        value.update(outline_id="outline-1", section_id="entry", section_title="入口与参数", section_objective="核对参数")
    return value


@pytest.mark.parametrize("section", [False, True])
def test_anchored_goal_preserved_across_tool_feedback_with_this_task_citations(section):
    goal = SECTION_GOAL if section else OUTLINE_GOAL
    def finish(context):
        assert context.goal == goal and context.anchor == ModelContext(**CONTEXT, goal=goal, anchor=anchor(section)).anchor
        return {"kind": "final", "final_output": (explanation if section else outline)("evidence-1")}
    result = run(ScriptedAdapter([tool_turn(), finish]), context={**CONTEXT, "goal": goal, "anchor": anchor(section)})
    assert result.status == "completed" and result.citations_validated and not result.semantic_verified
    assert "学习响应" in result.message


@pytest.mark.parametrize("problem", ["wrong_goal", "wrong_section", "foreign_evidence"])
def test_wrong_goal_selection_and_unread_citation_cannot_publish(problem):
    output = explanation("unread" if problem == "foreign_evidence" else "evidence-1")
    if problem == "wrong_section": output["section_id"] = "other"
    if problem == "wrong_goal": output = directory_turn()["final_output"]
    result = run(ScriptedAdapter([tool_turn(), {"kind": "final", "final_output": output}]),
                 context={**CONTEXT, "goal": SECTION_GOAL, "anchor": anchor(True)})
    assert result.status == "failed" and result.final_output is None


def test_learning_goal_cannot_override_instructions_or_omit_saved_selection():
    context = ModelContext(**CONTEXT, goal=OUTLINE_GOAL, anchor=anchor())
    assert context.system_instruction == OUTLINE_INSTRUCTION
    assert ModelContext.model_validate(context).system_instruction == OUTLINE_INSTRUCTION
    with pytest.raises(ValidationError):
        ModelContext(**CONTEXT, goal=OUTLINE_GOAL, anchor=anchor(), system_instruction=SYSTEM_INSTRUCTION)
    with pytest.raises(ValidationError): ModelContext(**CONTEXT, goal=SECTION_GOAL, anchor=anchor())
