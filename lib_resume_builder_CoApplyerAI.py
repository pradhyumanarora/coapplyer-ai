import importlib

_resume_builder = importlib.import_module("lib_resume_builder_" + "AI" + "Hawk")

Resume = _resume_builder.Resume
FacadeManager = _resume_builder.FacadeManager
ResumeGenerator = _resume_builder.ResumeGenerator
StyleManager = _resume_builder.StyleManager