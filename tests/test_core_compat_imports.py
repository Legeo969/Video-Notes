import importlib


def test_core_frame_service_imports():
    module = importlib.import_module("src.application.services.frame_service")
    assert hasattr(module, "FrameService")
    service = module.FrameService()
    assert callable(service.extract)
    assert callable(service._analyze_ocr)
