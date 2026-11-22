
import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

client = Groq(api_key=os.environ["GROQ_API_KEY"])

messages = [
    {
        "role": "system",
        "content": "You are a helpful AI assistant."
    }
]

print("Groq GPT-OSS 20B Chat")
print("Type 'exit' to quit.\n")

while True:
    user_input = input("You: ").strip()

    if user_input.lower() in ["exit", "quit"]:
        print("Chat ended.")
        break

    if not user_input:
        continue

    messages.append({
        "role": "user",
        "content": user_input
    })

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            temperature=0.2,
            max_completion_tokens=2048,
            reasoning_effort="low"
        )

        answer = response.choices[0].message.content or ""

        print(f"\nAI: {answer}\n")

        messages.append({
            "role": "assistant",
            "content": answer
        })

    except Exception as e:
        messages.pop()
        print(f"\nError: {e}\n")
