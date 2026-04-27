import os
import json
import subprocess
import yaml
import requests
from datetime import datetime
from pathlib import Path

OPENROUTER_KEY = os.environ["OPENROUTER_API_KEY"]
VAULT_DIR = Path("vault")
PROCESSED_FILE = Path("processed.txt")
LINKS_FILE = Path("reels_list.txt")

def get_unprocessed_links():
    if not LINKS_FILE.exists():
        print(f"ERROR: {LINKS_FILE} not found!")
        return []
    with open(LINKS_FILE) as f:
        all_links = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    if PROCESSED_FILE.exists():
        with open(PROCESSED_FILE) as f:
            processed = set(l.strip() for l in f)
    else:
        processed = set()
    return [l for l in all_links if l not in processed]

def scrape_reel(url):
    cmd = [
        "yt-dlp",
        "--write-info-json",
        "--skip-download",
        "--output", "temp/%(id)s.%(ext)s",
        "--no-playlist",
        "--max-filesize", "100m",
        url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    json_files = list(Path("temp").glob("*.info.json"))
    if not json_files:
        # Try to find any file with json extension
        json_files = list(Path("temp").glob("*.json"))
    if not json_files:
        print(f"  No JSON found. yt-dlp output: {result.stderr[:500]}")
        return None
    
    with open(json_files[0], encoding="utf-8") as f:
        info = json.load(f)
    
    for f in Path("temp").glob("*"):
        try:
            f.unlink()
        except:
            pass
    
    return {
        "id": info.get("id", ""),
        "title": info.get("title", ""),
        "description": info.get("description", ""),
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date", ""),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "tags": info.get("tags", []),
        "original_url": url
    }

def analyze_with_mistral(metadata):
    title = metadata.get("title", "")[:200]
    description = metadata.get("description", "")[:500]
    tags = metadata.get("tags", [])[:10]
    
    prompt = f"""Analyze this Instagram Reel. Return ONLY valid JSON, no other text.

Title: {title}
Description: {description}
Tags: {', '.join(tags)}

Return this exact JSON structure:
{{"title_clean": "Clean title here", "summary": "2-3 sentence summary here", "categories": ["Category1", "Category2"], "rating_1_to_10": 7, "key_concepts": ["Concept1", "Concept2", "Concept3"], "sentiment": "positive", "actionable_insight": "One actionable takeaway here", "related_topics": ["Topic1", "Topic2"]}}"""

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/reeler-code/reels-a-base",
            "X-Title": "Reels Knowledge Base"
        },
        json={
            "model": "mistralai/mistral-7b-instruct:free",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 500
        },
        timeout=90
    )
    
    if response.status_code == 429:
        raise Exception("Rate limited. Will retry next run.")
    
    if response.status_code != 200:
        raise Exception(f"API Error {response.status_code}: {response.text[:300]}")
    
    data = response.json()
    
    if "choices" not in data or len(data["choices"]) == 0:
        raise Exception(f"No choices in response: {json.dumps(data)[:300]}")
    
    content = data["choices"][0]["message"]["content"]
    content = content.replace("```json", "").replace("```", "").strip()
    
    # Find JSON object
    start = content.find("{")
    end = content.rfind("}") + 1
    if start >= 0 and end > start:
        content = content[start:end]
    
    return json.loads(content)

def create_markdown(metadata, analysis):
    date_str = metadata.get("upload_date", datetime.now().strftime("%Y%m%d"))
    if date_str and len(date_str) == 8:
        date_formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    else:
        date_formatted = datetime.now().strftime("%Y-%m-%d")
    
    title = analysis.get("title_clean", metadata.get("title", "Untitled"))
    slug = "".join(c if c.isalnum() or c in "- " else "" for c in title.lower())
    slug = slug.replace(" ", "-")[:50]
    filename = f"{date_formatted}-{slug}.md"
    
    frontmatter = {
        "title": title,
        "date": date_formatted,
        "source": metadata.get("original_url", ""),
        "duration": metadata.get("duration"),
        "rating": analysis.get("rating_1_to_10", 5),
        "categories": analysis.get("categories", []),
        "sentiment": analysis.get("sentiment", "neutral"),
        "key_concepts": analysis.get("key_concepts", []),
        "related_topics": analysis.get("related_topics", []),
        "likes": metadata.get("like_count"),
        "comments": metadata.get("comment_count"),
        "instagram_tags": metadata.get("tags", [])
    }
    
    md = "---\n"
    md += yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)
    md += "---\n\n"
    md += f"# {title}\n\n"
    
    rating = analysis.get("rating_1_to_10", 5)
    stars = "⭐" * min(rating, 10)
    md += f"**Rating:** {stars} ({rating}/10)\n\n"
    md += f"**Sentiment:** {analysis.get('sentiment', 'neutral')}\n\n"
    md += f"**Source:** [Instagram Reel]({metadata.get('original_url', '')})\n\n"
    
    duration = metadata.get("duration")
    if duration:
        md += f"**Duration:** {duration}s\n\n"
    
    md += "---\n\n"
    md += f"## 📝 Summary\n\n{analysis.get('summary', 'No summary available.')}\n\n"
    md += f"## 💡 Actionable Insight\n\n{analysis.get('actionable_insight', 'No insight available.')}\n\n"
    md += f"## 🔑 Key Concepts\n\n"
    for concept in analysis.get("key_concepts", []):
        md += f"- [[{concept}]]\n"
    md += "\n## 🔗 Related Topics\n\n"
    for topic in analysis.get("related_topics", []):
        md += f"- [[{topic}]]\n"
    md += f"\n## 📂 Categories\n\n"
    for cat in analysis.get("categories", []):
        md += f"- [[{cat}]]\n"
    
    desc = metadata.get("description", "")
    if desc:
        md += f"\n## 📄 Original Description\n\n{desc}\n\n"
    
    md += f"## 🔍 Connected Reels\n\n```dataview\nLIST\nFROM [[{title}]]\nSORT rating DESC\n```\n"
    
    filepath = VAULT_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    counter = 1
    while filepath.exists():
        base = filename.replace(".md", "")
        filepath = VAULT_DIR / f"{base}-{counter}.md"
        counter += 1
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md)
    
    return str(filepath)

def mark_processed(url):
    with open(PROCESSED_FILE, "a") as f:
        f.write(url + "\n")

# ========== MAIN ==========
VAULT_DIR.mkdir(exist_ok=True)
Path("temp").mkdir(exist_ok=True)

links = get_unprocessed_links()
print(f"Found {len(links)} unprocessed reels")

if len(links) == 0:
    print("No new reels to process. Exiting.")
    exit(0)

success_count = 0
for i, url in enumerate(links):
    try:
        print(f"\n[{i+1}/{len(links)}] Processing: {url}")
        
        print("  Downloading metadata...")
        metadata = scrape_reel(url)
        if not metadata:
            print("  ⚠️ Could not download, skipping")
            mark_processed(url)
            continue
        
        print("  Analyzing with Mistral AI...")
        analysis = analyze_with_mistral(metadata)
        
        print("  Creating markdown file...")
        filepath = create_markdown(metadata, analysis)
        
        mark_processed(url)
        success_count += 1
        print(f"  ✅ Created: {filepath}")
        
    except Exception as e:
        print(f"  ❌ Error: {str(e)[:300]}")
        mark_processed(url)

print(f"\nDone! Processed {success_count}/{len(links)} successfully.")
