

# AI服务器模型及API调用说明

### **1、大语言模型**
- **模型名称：**Qwen3-30B-A3B（混合专家模型）
- **参数规模：**总参数 300 亿，每次推理激活其中的 30 亿参数。
- **模型架构：**包含 48 层，128 个专家（每个任务激活 8 个），采用了混合专家模型架构。
- **上下文长度：**原生支持 32,768 个 token 的上下文窗口。
- **工具调用：**原生支持MCP协议，在工具调用等 Agent 任务中表现较好。
- **使用方法：**可使用OpenAI兼容的API接口进行访问，调试时可使用临时授权sk-1234567890（全公司共享），正式业务系统可单独联系研发中心申请。
- **具体调试方法如下：**

```bash
curl http://10.10.10.245:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-1234567890" \
  -d '{
    "messages": [{"role": "user", "content": "写一首关于春天的诗"}],
    "stream": true
  }'
```
​		**说明：**非流式请求请将stream改为false。

### **2、向量嵌入模型**

- **模型名称：**bge-m3
- **多功能性**：能够同时执行嵌入模型的三种常见检索功能：密集检索、多向量检索和稀疏检索。
- **多语言性**：支持超过100种工作语言。
- **多粒度**：能够处理不同粒度的输入，从短句到长达8192个令牌的长文档。
- **使用方法：**可使用OpenAI兼容的API接口进行访问，调试时可使用临时授权sk-1234567890（全公司共享），正式业务系统可单独联系研发中心申请。
- **具体调试方法如下：**

```bash
curl http://10.10.10.245:8000/v1/embeddings \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-1234567890" \
  -d '{
  "input": ["这是一个测试句子", "另一个需要嵌入的句子"]
}'
```
### **3、文档重排序模型**

- **模型名称：**bge-reranker-v2-m3
- **说明**：重排序器以问题和文档作为输入，直接输出相似度而非嵌入。您可以通过向重排序器输入查询和文章来获得相关性分数。该分数可通过sigmoid函数映射到[0,1]区间内的浮点值。
- **使用方法：**可使用OpenAI兼容的API接口进行访问，调试时可使用临时授权sk-1234567890（全公司共享），正式业务系统可单独联系研发中心申请。

```bash
curl http://10.10.10.245:8000/rerank \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-1234567890" \
  -d '{
    "query": "什么是自然语言处理？",
    "documents": ["自然语言处理是人工智能的一个分支", "机器学习是人工智能的核心技术"],
	"top_n": 2
  }'
```

### **4、语音转文本模型**

- **模型名称：**whisper-large-zh-cv11
- **说明**：基于 openai/whisper-large-v2 在中文（普通话）上的微调版本，使用了 Common Voice 11的训练集和验证集。
- **使用方法：**可使用OpenAI兼容的API接口进行访问，调试时可使用临时授权sk-1234567890（全公司共享），正式业务系统可单独联系研发中心申请。

```bash
curl http://10.10.10.245:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer sk-1234567890" \
  -F "file=@/home/sprixin/models/1.mp3" \
  -F "language=zh"
```

说明：语音模型使用whisper-large-zh-cv11，支持格式：mp3、wav、flac、m4a等格式，其他格式可通过ffmpeg进行转换。

### **5、文生图模型**

- **模型名称：**Qwen-Image-2512
- **说明**：基于 Qwen-Image的更新版本。
- **增强的人像真实感**：Qwen-Image-2512 显著减少了“AI 生成”的痕迹，大幅提升了整体图像的真实感，尤其在人物表现方面。
- **更精细的自然细节**：Qwen-Image-2512 在风景、动物毛发及其他自然元素的渲染上呈现出明显更丰富的细节。
- **改进的文字渲染能力**：Qwen-Image-2512 提高了文本元素的准确性和质量，在排版和多模态（文本+图像）组合方面更加忠实可靠。
- **使用方法：**可使用OpenAI兼容的API接口进行访问，调试时可使用临时授权sk-1234567890（全公司共享），正式业务系统可单独联系研发中心申请。

```bash
curl -X POST http://10.10.10.245:8000/v1/images/generations \
  -H "Authorization: Bearer sk-1234567890" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "生成一张猫狗大战的照片",
    "size": "1024x1024",
    "seed": 42
  }' | jq -r '.data[0].b64_json' | base64 -d > cat.png
```

### **6、千问多模态模型**

- **模型名称：**Qwen3-VL-8B-Instruct
- **定位**：通用型视觉-语言模型，旨在实现图文理解、推理与交互的完整闭环。
- **核心目标**：以轻量化参数（8B）实现接近百亿参数模型的性能，降低多模态AI的部署门槛。
- **典型场景**：智能客服、内容审核、工业质检、移动端AI应用等。
- 优势场景：
  - 移动端/边缘计算（如智能相册、实时翻译、AR应用）。
  - 工业质检（如微小瑕疵检测、装配缺陷识别）。
  - 交互式AI应用（如智能客服、内容审核、GUI代理操作）。
- 局限：
  - 在空间智能专项评测中表现相对局限（如VSI基准测试得分低于商汤模型）。
  - 对极端复杂文档的解析能力可能弱于专用OCR模型。

```bash
curl http://10.10.12.64:9996/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-VL-8B-Instruct",
    "messages": [{"role": "user", "content": "你好，用一句话介绍自己"}]
  }'
```

### **7、飞桨多模态模型**

- **模型名称：**PaddleOCR-VL（**受制于硬件资源，该模型仅在需要时启动，有需求请联系研发中。**）
- **定位**：专为文档解析场景优化的超轻量级视觉-语言模型，聚焦OCR（光学字符识别）任务。
- **核心目标**：在资源受限环境下实现高精度、多语言的文档解析，支持结构化输出（如JSON、Markdown）。
- **典型场景**：发票识别、学术论文解析、多语言合同处理等。
- 优势场景：
  - 企业级文档数字化（如合同、财报、学术文献）。
  - 多语言OCR需求（覆盖109种语言）。
  - 对解析精度要求极高，且资源充足的环境。
- 局限：
  - 显存占用高，边缘设备部署困难。
  - 功能聚焦于文档解析，缺乏通用多模态能力。

```bash
# 替换为你的图片路径（支持jpg/png/bmp等）
image_path="./1.png"

# 生成Base64编码（自动处理格式）
img_base64=$(base64 -i "$image_path" | awk '{printf "%s", $0}' | sed 's/$/\n/')
img_base64_with_prefix="data:image/jpeg;base64,$img_base64"

server_url="http://10.10.12.64:3010/v1/chat/completions"

curl -X POST "$server_url" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "PaddleOCR-VL-0.9B",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "请识别图片中的所有文字和数字，输出结构化结果，包含每个内容的文本、置信度和位置坐标（x1,y1,x2,y2），结果用JSON格式返回"
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "'"$img_base64_with_prefix"'"
            }
          }
        ]
      }
    ],
    "temperature": 0.0
  }'
```

### **8、图片编辑模型**

- **模型名称：**Qwen-Image-Edit-2511（**受制于硬件资源，该模型仅在需要时启动，有需求请联系研发中。**）
- **说明**：基于 Qwen-Image-Edit-2509的增强版本，该模型能够基于输入的人像进行富有想象力的编辑，同时保留主体的身份特征和视觉风格。

```bash
curl -s -D >(grep -i x-request-id >&2) \
  -o >(jq -r '.data[0].b64_json' | base64 --decode > edit_result2.jpeg) \
  -X POST "http://10.10.10.245:8000/v1/images/edits" \
  -F "model=/data/models/Qwen/Qwen-Image-Edit-2511" \
  -F "image=@./2.jpg" \
  -F "image=@./3.jpg" \
  -F "prompt=两个小朋友在一起，对视的动作" \
  -F "size=1024x1024" \
  -F "output_format=jpeg" \
  -F "num_inference_steps=50" \
  -F "guidance_scale=7.5" \
  -F "seed=123456"
```