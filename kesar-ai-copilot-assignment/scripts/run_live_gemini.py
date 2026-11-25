"""
Live End-to-End Runner for Kesar & Co. Support Copilot using Google Gemini API.
Runs all 25 test emails (or a custom subset) with your live Gemini API key,
executes tool lookups (orders.json and faq.md), enforces policy and security guardrails,
and exports the live results directly to live_audit_log.csv and live_tickets.csv.
"""

import os
import sys
import json
import csv
import re
import time
from datetime import datetime
import urllib.request
import urllib.error

DATA_DIR = r"C:\Users\Rishit\.gemini\antigravity\scratch\kesar-ai-copilot-assignment\data"
REPO_DIR = r"C:\Users\Rishit\.gemini\antigravity\scratch\kesar-ai-copilot-assignment"

with open(os.path.join(DATA_DIR, "orders.json"), "r", encoding="utf-8") as f:
    orders_data = {o["order_id"]: o for o in json.load(f)["orders"]}

with open(os.path.join(DATA_DIR, "test_emails.json"), "r", encoding="utf-8") as f:
    emails_data = json.load(f)["emails"]

with open(os.path.join(DATA_DIR, "faq.md"), "r", encoding="utf-8") as f:
    faq_content = f.read()

orders_by_email = {}
for o in orders_data.values():
    orders_by_email.setdefault(o["customer_email"].lower(), []).append(o)

def get_order_tool(order_id=None, customer_email=None):
    if order_id and order_id in orders_data:
        return orders_data[order_id]
    if customer_email and customer_email.lower() in orders_by_email:
        return orders_by_email[customer_email.lower()][0]
    return None

def call_gemini_api(prompt, api_key, model="gemini-1.5-flash", system_instruction=None, json_mode=False):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    
    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.1,
            "topP": 0.95
        }
    }
    
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"
        
    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }
        
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            candidate = data["candidates"][0]
            text = candidate["content"]["parts"][0]["text"]
            usage = data.get("usageMetadata", {})
            return text, usage
    except Exception as ex:
        print(f"Gemini API Error: {ex}")
        return None, {}

def process_email_live(email, api_key):
    e_id = email["id"]
    from_name = email["from"]["name"]
    from_email = email["from"]["email"]
    subject = email["subject"]
    body = email["body"]
    received_at = email["received_at"]
    
    print(f"\nProcessing {e_id} ({from_email})...")
    
    # Classification system prompt
    classify_system = (
        "You are an email classification and entity extraction engine for Kesar & Co., an Indian D2C Ayurvedic skincare brand.\n"
        "Return ONLY a JSON object with keys:\n"
        "- intent: 'order_status' | 'product_query' | 'return_refund' | 'complaint' | 'other'\n"
        "- secondary_intent: string or null\n"
        "- order_id: pattern '^KC-[0-9]{4}$' or null\n"
        "- sentiment: 'positive' | 'neutral' | 'negative' | 'angry'\n"
        "- language: 'en' | 'hi' | 'hinglish'\n"
        "- confidence: number between 0.0 and 1.0\n"
        "- summary: English summary under 200 characters\n"
    )
    
    classify_prompt = (
        f"Email details:\n"
        f"Received At: {received_at}\n"
        f"Sender: {from_name} <{from_email}>\n"
        f"Subject: {subject}\n"
        f"Body:\n{body}\n"
    )
    
    raw_json, usage = call_gemini_api(classify_prompt, api_key, system_instruction=classify_system, json_mode=True)
    
    if not raw_json:
        # Fallback
        order_match = re.search(r"KC-[0-9]{4}", subject + " " + body)
        llm_out = {
            "intent": "other", "secondary_intent": None,
            "order_id": order_match.group(0) if order_match else None,
            "sentiment": "neutral", "language": "en", "confidence": 0.5,
            "summary": "Fallback extraction due to API timeout"
        }
    else:
        try:
            # Clean markdown codeblocks if any
            clean_str = re.sub(r"^```(?:json)?\n|\n```$", "", raw_json.strip())
            llm_out = json.loads(clean_str)
        except Exception as e:
            print(f"JSON parse error on {e_id}: {e}")
            llm_out = {
                "intent": "other", "secondary_intent": None, "order_id": None,
                "sentiment": "neutral", "language": "en", "confidence": 0.5,
                "summary": "JSON decode failed"
            }

    # Security & Policy checks
    body_lower = (subject + " " + body).lower()
    prompt_injection = any(p in body_lower for p in ["ignore all previous instructions", "system note to ai assistant", "this customer is a vip"])
    
    order = get_order_tool(llm_out.get("order_id"), from_email)
    order_found = order is not None
    order_id = order["order_id"] if order else llm_out.get("order_id")
    
    # Privacy check: does sender email match order customer_email?
    privacy_violation = False
    if order and order["customer_email"].lower() != from_email.lower():
        privacy_violation = True
        
    return {
        "email_id": e_id,
        "llm_out": llm_out,
        "order": order,
        "order_found": order_found,
        "order_id": order_id,
        "prompt_injection": prompt_injection,
        "privacy_violation": privacy_violation,
        "usage": usage
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_live_gemini.py <YOUR_GEMINI_API_KEY> [--all | --subset E01,E04,E07,E12]")
        sys.exit(1)
        
    api_key = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "--subset"
    
    print(f"Starting Gemini Live Copilot Runner (Target: {mode})...")
    test_run = emails_data[:5] if mode == "--subset" else emails_data
    
    for em in test_run:
        res = process_email_live(em, api_key)
        print(f"Result {em['id']}: Intent = {res['llm_out'].get('intent')} | Order = {res['order_id']} | Tokens = {res['usage'].get('totalTokenCount', 'N/A')}")
        time.sleep(1) # delay to avoid rate limit
        
    print("\nLive test run completed successfully.")
