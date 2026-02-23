"""priorauth-tao: Bittensor Subnet Miner
Processes prior authorization requests using Claude AI
and returns structured approval/denial decisions scored by validators.
"""
import os
import time
import json
import asyncio
from dataclasses import dataclass
from typing import Optional
import anthropic
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="PriorAuth-Tao Miner", version="1.0.0")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# --- Data Models ---
class PARequest(BaseModel):
    request_id: str
    patient_age: int
    diagnosis_codes: list[str]  # ICD-10
    procedure_codes: list[str]  # CPT
    medication: Optional[str] = None
    clinical_notes: str
    insurance_plan: str
    prior_treatments: Optional[list[str]] = []

class PADecision(BaseModel):
    request_id: str
    approved: bool
    rationale: str
    confidence: float
    suggested_alternatives: list[str]
    appeal_guidance: Optional[str] = None
    processing_time_ms: int

# --- PAYER GUIDELINES (simplified MCG/InterQual-like rules) ---
PAYER_GUIDELINES = {
    "default": """
    Standard prior authorization criteria:
    - Medical necessity must be established
    - Conservative treatments must have been attempted first
    - Diagnosis must align with requested procedure/medication
    - Patient age and comorbidities must be considered
    - Cost-effective alternatives should be evaluated
    """
}

# --- Claude-powered PA Decision Engine ---
async def process_pa_with_claude(request: PARequest) -> PADecision:
    start = time.time()
    
    guidelines = PAYER_GUIDELINES.get(request.insurance_plan, PAYER_GUIDELINES["default"])
    
    prompt = f"""You are a clinical prior authorization specialist AI.
    
Review this PA request against evidence-based medical guidelines:

Patient: {request.patient_age} years old
Diagnosis (ICD-10): {', '.join(request.diagnosis_codes)}
Requested Procedure/Service (CPT): {', '.join(request.procedure_codes)}
Medication: {request.medication or 'N/A'}
Prior treatments tried: {', '.join(request.prior_treatments) if request.prior_treatments else 'None documented'}
Clinical Notes: {request.clinical_notes}
Insurance Plan: {request.insurance_plan}

Payer Guidelines:
{guidelines}

Provide your decision as JSON:
{{
  "approved": true/false,
  "rationale": "clinical reasoning in 2-3 sentences",
  "confidence": 0.0-1.0,
  "suggested_alternatives": ["alternative1", "alternative2"],
  "appeal_guidance": "if denied, what additional info would support appeal"
}}

Base your decision purely on medical necessity criteria and clinical evidence."""
    
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    
    elapsed_ms = int((time.time() - start) * 1000)
    
    try:
        text = response.content[0].text
        # Extract JSON from response
        start_idx = text.find("{")
        end_idx = text.rfind("}") + 1
        decision_json = json.loads(text[start_idx:end_idx])
        
        return PADecision(
            request_id=request.request_id,
            approved=decision_json.get("approved", False),
            rationale=decision_json.get("rationale", ""),
            confidence=decision_json.get("confidence", 0.5),
            suggested_alternatives=decision_json.get("suggested_alternatives", []),
            appeal_guidance=decision_json.get("appeal_guidance"),
            processing_time_ms=elapsed_ms
        )
    except (json.JSONDecodeError, IndexError, KeyError) as e:
        return PADecision(
            request_id=request.request_id,
            approved=False,
            rationale=f"Processing error: {str(e)}. Manual review required.",
            confidence=0.0,
            suggested_alternatives=[],
            processing_time_ms=elapsed_ms
        )

# --- Validator Scoring (Bittensor subnet logic) ---
def score_decision(decision: PADecision, ground_truth: Optional[bool] = None) -> float:
    """Score miner decisions for Bittensor reward mechanism."""
    score = 0.0
    # Confidence calibration
    score += decision.confidence * 0.4
    # Completeness of rationale
    score += min(len(decision.rationale) / 200, 1.0) * 0.3
    # Alternatives provided
    score += min(len(decision.suggested_alternatives) / 3, 1.0) * 0.2
    # Appeal guidance (only for denials)
    if not decision.approved and decision.appeal_guidance:
        score += 0.1
    # Ground truth accuracy (when available from outcome data)
    if ground_truth is not None:
        accuracy_bonus = 0.3 if decision.approved == ground_truth else -0.1
        score = min(1.0, score + accuracy_bonus)
    return min(1.0, max(0.0, score))

# --- API Endpoints ---
@app.post("/process", response_model=PADecision)
async def process_authorization(request: PARequest):
    """Process a prior authorization request (miner endpoint)."""
    return await process_pa_with_claude(request)

@app.post("/score")
async def score_miner_decision(decision: PADecision, ground_truth: Optional[bool] = None):
    """Validator endpoint: score a miner's PA decision."""
    score = score_decision(decision, ground_truth)
    return {"request_id": decision.request_id, "score": score, "validator": "priorauth-tao-v1"}

@app.get("/health")
async def health():
    return {"status": "online", "subnet": "priorauth-tao", "model": "claude-opus-4-5"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
