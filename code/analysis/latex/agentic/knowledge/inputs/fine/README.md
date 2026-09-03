# Withheld: fine-level synthesis inputs

The other stages' inputs are prompts plus LaTeX written by the previous stage,
and they are included. This directory is different: it held one bundle per fine
theme listing every extracted finding, tagged with the citation key of the paper
it came from. Those findings are the extracted record of the source literature,
most of which is not open access, so they are not redistributed here.

`_units.json` remains: it defines each unit and the citation keys the writing
agent was permitted to use, which is what makes the citation bounding auditable.

The bundles are rebuilt by `synth_agentic/prep_inputs.py` from the extraction
tables, for anyone working from their own copies of the papers.
