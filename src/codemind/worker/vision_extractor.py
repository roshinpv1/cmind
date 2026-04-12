import os
import glob
import base64
from pathlib import Path
from codemind.graph.graph_db import GraphBuilder
from langchain_core.messages import HumanMessage
# Try importing standard LangChain generic chat model interface
from langchain_openai import ChatOpenAI 

class VisionGraphExtractor:
    """
    Implements Graphify's Multimodal extraction pipeline.
    Scans a repository for images (architecture diagrams) and markdown/PDFs,
    runs a Vision-capable LLM to extract semantic context, and injects
    'INFERRED' relationships into Graphify.
    """
    
    def __init__(self, repo_path: str, repo_id: str, graph_builder: GraphBuilder):
        self.repo_path = Path(repo_path)
        self.repo_id = repo_id
        self.graph = graph_builder
        # Optional: Initialize a vision capable model (Claude 3.5 Sonnet / GPT-4o)
        # Here we mock out the direct initialization to allow dependency injection
        # but provide a default standard.
        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key:
            self.vision_llm = ChatOpenAI(model="gpt-4o", max_tokens=2000)
        else:
            self.vision_llm = None
            
    def _encode_image(self, image_path: Path) -> str:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def run_extraction(self, run_multimodal: bool = False):
        """
        Main entrypoint. Skips if run_multimodal is False to save tokens.
        """
        if not run_multimodal:
            print("[VISION] Multimodal graph extraction disabled. Skipping.")
            return

        if not self.vision_llm:
            print("[VISION] No Vision LLM configured. Please set OPENAI_API_KEY. Skipping.")
            return

        print(f"[VISION] Starting multimodal graph extraction for {self.repo_id}...")
        
        # 1. Scan for images and architecture md files
        image_extensions = ["*.png", "*.jpg", "*.jpeg"]
        target_files = []
        for ext in image_extensions:
            # Recursively find images, limit to standard dirs
            for file_path in self.repo_path.rglob(ext):
                # Ignore node_modules, temp
                if "node_modules" not in str(file_path):
                    target_files.append(file_path)

        for img_path in target_files:
            self._process_image(img_path)

    def _process_image(self, image_path: Path):
        """Processes a single architecture diagram and injects graph edges."""
        print(f"[VISION] Analyzing diagram: {image_path.name}")
        
        base64_img = self._encode_image(image_path)
        
        prompt = (
            "You are an expert system architect mapping out a Graphify property graph. "
            "Analyze the provided architecture diagram. Identify the literal code files "
            "(e.g., 'auth.py', 'database.go') or abstract components that are connecting to each other. "
            "Output a mapping of relationships formatted exactly as:\n"
            "SOURCE_FILE | TARGET_FILE | CONFIDENCE_0_TO_1 | REASONING\n"
            "Only output the relations, no markdown formatting."
        )

        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"},
                },
            ]
        )

        try:
            response = self.vision_llm.invoke([message])
            relations_text = response.content.strip().split("\\n")
            
            for line in relations_text:
                if "|" not in line: continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) == 4:
                    source, target, conf_str, reason = parts
                    try:
                        confidence = float(conf_str)
                    except:
                        confidence = 0.5
                        
                    # Inject mapping into Graphify
                    source_id = f"file:{self.repo_id}:{source}"
                    target_id = f"file:{self.repo_id}:{target}"
                    
                    self.graph.build_semantic_link(
                        repo_id=self.repo_id,
                        node_type="File",
                        source_id=source_id,
                        target_id=target_id,
                        provenance="INFERRED",
                        confidence=confidence,
                        reasoning=f"Vision Extraction from {image_path.name}: {reason}"
                    )
            print(f"[VISION] Mapped semantic inferences for {image_path.name}")
        except Exception as e:
            print(f"[VISION] Failed to extract semantics from {image_path.name}: {str(e)}")
