import os
import json
import time
from decimal import Decimal
from typing import Dict, Any, List
from backend.services.validator import to_decimal, close_enough, normalize_gstin


def evaluate_extraction_against_ground_truth(
    extracted: Dict[str, Any],
    ground_truth: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Compares an extraction result against an approved ground-truth invoice.
    Produces precision metrics across critical accounting fields.
    """
    field_matches = {}
    mismatches = []

    # 1. Invoice Number (Exact)
    ext_inv = (extracted.get("metadata", {}).get("invoice_number") or "").strip().upper()
    gt_inv = (ground_truth.get("invoice_number") or "").strip().upper()
    inv_match = (ext_inv == gt_inv) if gt_inv else True
    field_matches["invoice_number"] = inv_match
    if not inv_match:
        mismatches.append(f"invoice_number: expected '{gt_inv}', got '{ext_inv}'")

    # 2. Seller GSTIN (Exact)
    ext_sgstin = normalize_gstin(extracted.get("seller", {}).get("gstin"))
    gt_sgstin = normalize_gstin(ground_truth.get("seller_gstin"))
    sgstin_match = (ext_sgstin == gt_sgstin) if gt_sgstin else True
    field_matches["seller_gstin"] = sgstin_match
    if not sgstin_match:
        mismatches.append(f"seller_gstin: expected '{gt_sgstin}', got '{ext_sgstin}'")

    # 3. Buyer GSTIN (Exact)
    ext_bgstin = normalize_gstin(extracted.get("buyer", {}).get("gstin"))
    gt_bgstin = normalize_gstin(ground_truth.get("buyer_gstin"))
    bgstin_match = (ext_bgstin == gt_bgstin) if gt_bgstin else True
    field_matches["buyer_gstin"] = bgstin_match
    if not bgstin_match:
        mismatches.append(f"buyer_gstin: expected '{gt_bgstin}', got '{ext_bgstin}'")

    # 4. Grand Total (within ₹1.00 tolerance)
    ext_grand = to_decimal(extracted.get("summary", {}).get("grand_total"))
    gt_grand = to_decimal(ground_truth.get("grand_total"))
    grand_match = close_enough(ext_grand, gt_grand, "1.00")
    field_matches["grand_total"] = grand_match
    if not grand_match:
        mismatches.append(f"grand_total: expected '{gt_grand}', got '{ext_grand}'")

    # 5. Total Tax (within ₹1.00 tolerance)
    if "total_tax" in ground_truth:
        ext_tax = to_decimal(extracted.get("summary", {}).get("total_gst"))
        gt_tax = to_decimal(ground_truth.get("total_tax"))
        tax_match = close_enough(ext_tax, gt_tax, "1.00")
        field_matches["total_tax"] = tax_match
        if not tax_match:
            mismatches.append(f"total_tax: expected '{gt_tax}', got '{ext_tax}'")

    # 6. Line Items Count & Data
    ext_items = extracted.get("line_items", [])
    gt_items = ground_truth.get("line_items", [])
    count_match = (len(ext_items) == len(gt_items)) if gt_items else True
    field_matches["line_items_count"] = count_match
    if not count_match:
        mismatches.append(f"line_items_count: expected {len(gt_items)}, got {len(ext_items)}")

    # 7. Semantic Quality / Classification Audit
    # Crucial Rule: If mismatches exist, extraction must NOT be marked SUCCESS
    status = extracted.get("status", "NEEDS_REVIEW")
    is_false_positive_success = (len(mismatches) > 0 and status == "SUCCESS")

    return {
        "passed_all": len(mismatches) == 0,
        "mismatches_count": len(mismatches),
        "mismatches": mismatches,
        "field_matches": field_matches,
        "false_positive_success": is_false_positive_success,
        "assigned_status": status
    }


def run_ground_truth_benchmark_suite(test_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Executes a full regression test across ground truth fixtures and produces a comprehensive audit report.
    """
    total_cases = len(test_cases)
    if total_cases == 0:
        return {"total_cases": 0, "message": "No test cases provided."}

    perfect_runs = 0
    false_positives = 0
    field_accuracies = {
        "invoice_number": 0,
        "seller_gstin": 0,
        "buyer_gstin": 0,
        "grand_total": 0,
        "total_tax": 0,
        "line_items_count": 0
    }

    results = []
    for tc in test_cases:
        eval_res = evaluate_extraction_against_ground_truth(tc["extracted"], tc["ground_truth"])
        results.append({
            "test_id": tc.get("id", "case"),
            "filename": tc.get("filename", "unknown"),
            "eval": eval_res
        })
        if eval_res["passed_all"]:
            perfect_runs += 1
        if eval_res["false_positive_success"]:
            false_positives += 1

        for k, v in eval_res["field_matches"].items():
            if k in field_accuracies and v:
                field_accuracies[k] += 1

    accuracy_percentages = {
        k: round((v / float(total_cases)) * 100.0, 2)
        for k, v in field_accuracies.items()
    }

    overall_accuracy = round((perfect_runs / float(total_cases)) * 100.0, 2)

    return {
        "total_cases": total_cases,
        "perfect_matches": perfect_runs,
        "overall_accuracy_pct": overall_accuracy,
        "false_positives_count": false_positives,
        "zero_false_positive_target_met": (false_positives == 0),
        "field_accuracies_pct": accuracy_percentages,
        "case_details": results
    }
