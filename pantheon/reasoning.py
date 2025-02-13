from .utils.llm import import_litellm


def use_reasoning_model(model):
    async def reasoning(question: str) -> str:
        """Use reasoning model to solve complex problems.
        Especially useful for math, logic, physics, programming and other complex problems."""
        litellm = import_litellm()
        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": question}],
        )
        return response["choices"][0]["message"]["content"]
    return reasoning


reasoning_o1 = use_reasoning_model("o1")
reasoning_o1_mini = use_reasoning_model("o1-mini")
reasoning_o3_mini = use_reasoning_model("o3-mini")
reasoning_flash_thinking_2 = use_reasoning_model("gemini/gemini-2.0-flash-thinking-exp")
reasoning_deepseek_reasoner = use_reasoning_model("deepseek/deepseek-reasoner")
