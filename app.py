#!/usr/bin/env python3

import aws_cdk as cdk

from tech_summit.tech_summit_stack import TechSummitStack


app = cdk.App()
TechSummitStack(app, "TechSummitStack")

app.synth()
