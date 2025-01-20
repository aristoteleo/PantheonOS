from litellm import acompletion


def use_reasoning_model(model):
    async def reasoning(question: str) -> str:
        """Use reasoning model to solve complex problems.
        Especially useful for math, physics, programming, and other complex problems."""
        response = await acompletion(
            model=model,
            messages=[{"role": "user", "content": question}],
        )
        return response["choices"][0]["message"]["content"]
    return reasoning


reasoning_o1 = use_reasoning_model("o1")
reasoning_o1_mini = use_reasoning_model("o1-mini")
reasoning_flash_thinking_2 = use_reasoning_model("gemini/gemini-2.0-flash-thinking-exp")
