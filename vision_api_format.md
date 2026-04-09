# Azure OpenAI Responses API - Image Input Format

```python
response = client.responses.create(
    model="gpt-4o",
    input=[
        {
            "role": "user",
            "content": [
                { "type": "input_text", "text": "what is in this image?" },
                {
                    "type": "input_image",
                    "image_url": "<image_URL>"
                }
            ]
        }
    ]
)
```

Key differences from Chat Completions API:
- Chat Completions: type="image_url", image_url={"url": "..."}
- Responses API: type="input_image", image_url="<url>"
