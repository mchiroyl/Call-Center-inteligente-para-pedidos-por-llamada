import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))


class WorkflowRuleTests(unittest.TestCase):
    def setUp(self):
        import workflow

        self.workflow = workflow.CallCenterWorkflow()
        self.catalog = {
            "burger-clasica": {"id": "burger-clasica", "category": "hamburguesas"},
            "burger-doble-bacon": {"id": "burger-doble-bacon", "category": "hamburguesas"},
            "refresco-cola": {"id": "refresco-cola", "category": "bebidas"},
            "agua-botella": {"id": "agua-botella", "category": "bebidas"},
        }

    def test_infers_specific_items_from_common_voice_phrases(self):
        result = self.workflow._post_process_extraction(
            "Agregame tres aguas puras.",
            {"intent": "unknown", "items": [], "asks_question": False},
            self.catalog,
        )

        self.assertEqual(result["intent"], "add_item")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["product_id"], "agua-botella")
        self.assertEqual(result["items"][0]["quantity"], 3)

    def test_generic_hamburger_request_requires_clarification(self):
        result = self.workflow._post_process_extraction(
            "Hamburguesa necesito dos.",
            {"intent": "unknown", "items": [], "asks_question": False},
            self.catalog,
        )

        self.assertFalse(result["items"])
        self.assertIn("hamburguesa clasica", result["clarification_needed"].lower())
        self.assertIn("doble bacon", result["clarification_needed"].lower())

    def test_total_question_is_forced_into_question_mode(self):
        result = self.workflow._post_process_extraction(
            "Que hiciste con lo que te dije de hamburguesas, lo agregaste al pedido o no.",
            {"intent": "unknown", "items": [], "asks_question": False},
            self.catalog,
        )

        self.assertEqual(result["intent"], "ask_question")
        self.assertTrue(result["asks_question"])

    def test_no_need_anything_becomes_cancel_order(self):
        result = self.workflow._post_process_extraction(
            "No necesito nada, gracias.",
            {"intent": "unknown", "items": [], "asks_question": False},
            self.catalog,
        )

        self.assertEqual(result["intent"], "cancel_order")
        self.assertFalse(result["asks_question"])

    def test_confirmation_variants_are_detected(self):
        positives = (
            "Si correcto.",
            "Confirmo.",
            "Confirmo el pedido.",
            "Si, confirmo el pedido.",
            "Esta bien, dale.",
        )
        for phrase in positives:
            with self.subTest(phrase=phrase):
                self.assertTrue(self.workflow._looks_like_confirmation(phrase))

    def test_non_confirmation_phrase_is_not_detected(self):
        self.assertFalse(self.workflow._looks_like_confirmation("Gracias."))

    def test_call_end_variants_are_detected(self):
        positives = (
            "Gracias, eso es todo.",
            "Eso es todo, adios.",
            "Listo gracias.",
            "No necesito nada mas.",
        )
        for phrase in positives:
            with self.subTest(phrase=phrase):
                self.assertTrue(self.workflow._looks_like_call_end(phrase))


if __name__ == "__main__":
    unittest.main()
