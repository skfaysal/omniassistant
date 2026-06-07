from collections.abc import AsyncGenerator, Generator

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage

from agent import agent


def stream_response(messages: list[BaseMessage]) -> Generator[str, None, None]:
    """
    Sync generator — yields text chunks as they arrive.

    FastAPI usage:
        return StreamingResponse(stream_response(messages), media_type="text/plain")
    """
    for mode, data in agent.stream(
        {"messages": messages},
        stream_mode=["updates", "messages"],
    ):
        if mode == "messages":
            message_chunk, metadata = data
            node = metadata.get("langgraph_node", "")

            # Skip orchestrator tokens — it uses structured output so its
            # LLM tokens are raw JSON (e.g. '{"next":"text2sql"}'), not useful text
            if node == "orchestrator":
                continue

            if isinstance(message_chunk, AIMessageChunk) and isinstance(message_chunk.content, str) and message_chunk.content:
                yield message_chunk.content

        elif mode == "updates":
            for node_name, delta in data.items():
                if node_name == "orchestrator":
                    # Show the routing decision clearly
                    yield f"[Orchestrator → delegating to: {delta.get('next')}]\n\n"
                else:
                    # Sub-agent finished — label the block
                    yield f"\n\n[{node_name} finished]"




def run_cli() -> None:
    """CLI runner — the while loop lives here, not in the generator."""
    history: list[BaseMessage] = []
    print("Chatbot ready. Type 'exit' to quit.\n")

    while True:
        user_input = input("You: ").strip()
        if not user_input or user_input.lower() == "exit":
            break

        history.append(HumanMessage(content=user_input))
        print("Assistant: ", end="", flush=True)

        reply_parts: list[str] = []
        for chunk in stream_response(history):
            print(chunk, end="", flush=True)
            reply_parts.append(chunk)

        print("\n")
        history.append(AIMessage(content="".join(reply_parts)))


if __name__ == "__main__":
    run_cli()
