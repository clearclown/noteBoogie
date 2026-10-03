"""Answer listening questions using the selected chapter's original text."""

from typing import Literal

from ai_prompter import Prompter
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers.pydantic import PydanticOutputParser
from pydantic import BaseModel, ConfigDict, Field

from open_notebook.ai.provision import provision_langchain_model
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.exceptions import (
    ExternalServiceError,
    InvalidInputError,
    NotFoundError,
    OpenNotebookError,
)
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.text_utils import clean_thinking_content, extract_text_content


class ChapterConversationMessage(BaseModel):
    """Bounded conversational context; never treated as source evidence."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChapterQuestionRequest(BaseModel):
    """The episode ID comes from the route, not model-generated chapter labels."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    history: list[ChapterConversationMessage] = Field(
        default_factory=list, max_length=6
    )


class ChapterAnswerDraft(BaseModel):
    """Structured model output with verbatim evidence for server validation."""

    answer: str = Field(max_length=8000)
    excerpts: list[str] = Field(max_length=3)


class ChapterQuestionResponse(ChapterAnswerDraft):
    episode_id: str
    chapter_title: str
    supported: bool


async def answer_chapter_question(
    episode_id: str, request: ChapterQuestionRequest
) -> ChapterQuestionResponse:
    """Load one stored chapter and reject answers with missing/invalid excerpts."""
    if not episode_id.startswith("episode:"):
        raise InvalidInputError("A chapter episode ID is required")
    try:
        record_id = ensure_record_id(episode_id)
    except (ValueError, TypeError) as exc:
        raise InvalidInputError("Invalid chapter episode ID") from exc

    rows = await repo_query(
        "SELECT content, name, chapter_title, chapter_index, audiobook FROM $id",
        {"id": record_id},
    )
    if not rows:
        raise NotFoundError("Chapter not found")
    chapter = rows[0]
    if not chapter.get("audiobook") or chapter.get("chapter_index") is None:
        raise InvalidInputError("This episode is not an audiobook chapter")
    content = str(chapter.get("content") or "").strip()
    title = str(chapter.get("chapter_title") or chapter.get("name") or "")

    def result(
        answer: str = "", excerpts: list[str] | None = None
    ) -> ChapterQuestionResponse:
        return ChapterQuestionResponse(
            episode_id=episode_id,
            chapter_title=title,
            answer=answer,
            excerpts=excerpts or [],
            supported=bool(answer and excerpts),
        )

    if not content:
        return result()

    parser: PydanticOutputParser[ChapterAnswerDraft] = PydanticOutputParser(
        pydantic_object=ChapterAnswerDraft
    )
    prompt = Prompter(prompt_template="chapter_question").render(
        data={"format_instructions": parser.get_format_instructions()}
    )
    # Keep source data out of the system instruction and template source.
    messages: list[BaseMessage] = [
        SystemMessage(content=prompt),
        HumanMessage(content=content),
    ]
    for message in request.history:
        message_type = HumanMessage if message.role == "user" else AIMessage
        messages.append(message_type(content=message.content))
    messages.append(HumanMessage(content=request.question))
    try:
        model = await provision_langchain_model(
            str(messages), None, "chat", max_tokens=2500
        )
        response = await model.ainvoke(messages)
        draft = parser.parse(
            clean_thinking_content(extract_text_content(response.content))
        )
    except OutputParserException as exc:
        # Parser errors can contain the whole model output; keep book text out of logs.
        raise ExternalServiceError(
            "Invalid chapter answer format. Please try again."
        ) from exc
    except OpenNotebookError:
        raise
    except Exception as exc:
        error_class, error_message = classify_error(exc)
        raise error_class(error_message) from exc

    # Tolerate layout whitespace, but never show a fabricated source excerpt.
    normalized_source = " ".join(content.split())
    excerpts = list(dict.fromkeys(" ".join(q.split()) for q in draft.excerpts))
    if (
        not draft.answer.strip()
        or not excerpts
        or any(
            len(quote) < 8 or len(quote) > 600 or quote not in normalized_source
            for quote in excerpts
        )
    ):
        return result()
    return result(draft.answer.strip(), excerpts)
