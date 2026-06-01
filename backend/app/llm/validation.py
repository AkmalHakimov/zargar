from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.base import LLMProvider

T = TypeVar("T", bound=BaseModel)


class LLMValidationError(RuntimeError):
    pass


async def validated_json_completion(
    llm: LLMProvider,
    schema: type[T],
    system: str,
    user: str,
) -> T:
    data = await llm.json_completion(system, user)
    try:
        return schema.model_validate(data)
    except ValidationError as first_error:
        repair_user = (
            "The previous response did not match the required JSON schema.\n"
            f"Schema: {schema.model_json_schema()}\n"
            f"Validation error: {first_error}\n"
            f"Invalid response: {data}\n\n"
            "Return corrected JSON only for the original task."
        )
        repaired = await llm.json_completion(system, repair_user)
        try:
            return schema.model_validate(repaired)
        except ValidationError as second_error:
            raise LLMValidationError(str(second_error)) from second_error
