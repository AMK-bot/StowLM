/* StowLM 3-minute video: shot list for the stage director (stage-director.js, /stage?video=1). */
/* >>> THE ONE-LINE CHOICE: 'v6' is the film (plan v6, script-v6.md). 'v5' stays reachable only as ?variant=v5, a rehearsal. <<< */
window.STAGE_VARIANT = 'v6';
/*
 * Source script: experiments/mvp/video/script.md (draft 2: 11 segments, 2:54; shot 3 splits into 3a, 3b, 3c). Worse, Par and Better were retired on 31 Oct; their config
 * is kept at experiments/mvp/video/archive/stage-shots.worse-par-better.js. EDIT BY HAND; keep it valid JSON inside the braces
 * (the test parses it). Rules:
 *  - duration_s must sum to total_s. Times inside a shot are seconds from the shot start.
 *  - Captions are verbatim from the script's caption column. "extra": true marks a caption the script doesn't have (labels only).
 *  - {{NUMBER: what}} and {{REPO_URL}}: filled ONLY from stage-fillv5.js (experiments/mvp/video/fill-v5.json through
 *    experiments/mvp/ui/sync_fill.py). Unfilled: DRAFT shows a PENDING chip; final drops that " · " segment; a card reads
 *    "pending: <task>", never a blank bar or a zero. Nothing from STORY-NEEDS's provisional column goes in here.
 *  - "hero": true = the shot shows hero_plan when run 3.2 is in the catalogue, else the prepared brief case standin_case
 *    (its recorded plan, once the fresh gate shows it) under standin_tag, in every mode. "standin": "plan" uses standin_plan
 *    instead (shot 4: the plan with a bound Spark explanation). hero_sentence shows only with the hero; a stand-in shows the
 *    sentence it was really recorded under.
 *  - "when"/"otherwise": caption shown only if true of the plan shown.
 *  - "brief": the track driving window.StageBrief.show (app/static/stage-brief.js, owned by the brief flow); "stage": null hides it.
 *  - beats: shot 5's three beats, addressable as &shot=5a, 5b, 5c. clip_x: the X clip, spans of shots.
 *  - generated_voice: true adds the generated-voice line to the end card.
 *
 * Variant v6 (experiments/mvp/video/script-v6.md) carries its own shots, cards and measured block; everything below it is v5.
 *  - {{name}}: from stage-fillv6.js (fill-v6.json through sync_fill.py) or, for the hero, from the gate at boot. [[IF name: …]]
 *    is kept only if name is set. Unfilled: a chip in draft, a dropped segment in final. In v6 a card never says pending in final.
 *  - "v6": {chapter, from, to}: the director calls StageV6.seek under its dip, fitting the chapter (to defaults to its duration_s) to the shot. A gap plays "fallback",
 *    the script's "If cut" line: fallback keys replace the shot's, "give" hands its spare seconds to another shot.
 *  - No figure is typed in here. A measured number is a {{name}} from /api/v6/figures (app/v6.py, each bound to its source and sha256), or the gate at boot.
 *    The few with no machine-readable source are in experiments/mvp/ui/fixtures/film-figures.json. No source: that " · " segment drops.
 *  - "plan": {"label": "v6:before"}: the hero as `python -m app --v6` registers it. "show_tag": the stand-in tag sits in frame.
 */
window.STAGE_SHOTS = {
  "version": "video-v5",
  "variants": {
    "v5": {
      "script": "experiments/mvp/video/script.md",
      "total_s": 174,
      "kpi_money_hidden": true,
      "kpi_hidden": true
    },
    "v6": {
      "script": "experiments/mvp/video/script-v6.md",
      "total_s": 167,
      "kpi_money_hidden": true,
      "kpi_hidden": true,
      "fill": "v6",
      "lower_third": "Synthetic ships and cargo, simulated port",
      "hero_plan": null,
      "hero_sentence": "",
      "hero_slot": "",
      "standin_tag": "",
      "measured": {},
      "cards": {
        "sealed": {
          "eyebrow": "HELD-OUT TEST VOYAGES",
          "title": "{{sealed_envelope_pass}} of {{sealed_envelope_maps}} held-out test voyages inside the design limit, all full checker passes",
          "pending": "3.1",
          "fill_from": "experiments/mvp/video/fill-v6.json (p113), from experiments/mvp/pipeline/sealed/runs/final-3/story-figures.json",
          "scale_key": "sealed_envelope_maps",
          "unit": "",
          "rows": [
            {
              "label": "Full checker passes",
              "key": "sealed_envelope_pass",
              "own": true,
              "at": 1.0
            }
          ],
          "partial": "The first attempt matched the final run on {{overlap1_same}} of the {{overlap1_rows}} rows it opened (status and lifts; {{overlap1_verdict}} of {{overlap1_rows}} on verdict alone); the second on {{overlap2_same}} of {{overlap2_rows}}",
          "notes": [
            "frozen pipeline",
            "the third attempt: two earlier runs stopped on our own file guard and are published",
            "not the pre-registered sealed claim",
            "the planning step, from a clean load list",
            "up to 195 boxes in a half-ship section of feeder ships of 500 to 800 TEU",
            "the engine isn't competitive at large scale yet",
            "no lift comparison: the two-step baseline passed {{sealed_twostep_pass}} of {{sealed_twostep_maps}}, so there are no mutual passes",
            "Navis and human performance aren't public."
          ]
        },
        "forward": {
          "eyebrow": "Projection",
          "pending": "1.14",
          "big": "${{impact_bill_billion}} billion a year",
          "big_sub": "port-call cost, worldwide · every 1 % ≈ ${{impact_bill_step_billion}} billion",
          "headline": "≈ ${{impact_mid_billion}} billion a year",
          "sub": "the pool a better plan can act on · mid case · low ${{impact_low_billion}} billion",
          "fine": [
            "inputs: {{impact_teu_m}}M TEU, {{impact_calls_k}}k calls, assumed {{impact_restow_pct}} % restows at ${{impact_lift_usd}} a lift, {{impact_berth_pct}} % less berth time",
            "what would have to be true: full-ship, multi-port planning works; these rates hold worldwide; the extra work is avoidable; terms that permit operational use; none of it has been measured",
            "check: world terminal revenue ${{impact_revenue_low}}–{{impact_revenue_high}} billion a year"
          ]
        },
        "typed": {
          "eyebrow": "THE PLANNER TYPES IT",
          "title": "{{hero_sentence}}",
          "lines": []
        },
        "reading": {
          "eyebrow": "THE READER",
          "title": "StowLM Reader reads the words, the planner confirms",
          "lines": [
            "Recorded answer",
            "built to refuse anything else; it can slip, so the planner confirms"
          ]
        },
        "mask": {
          "eyebrow": "X-RAY OF THE MASK",
          "title": "A mask allows only legal rules, real bays and allowed classes",
          "lines": [
            "Legal can still be wrong, so a person confirms",
            "Explanation card, not a recording"
          ]
        },
        "checker": {
          "eyebrow": "THE CHECKER ANSWERS",
          "title": "Drag any box. The checker says which rule broke.",
          "lines": [
            "Every plan shown has been through the full checker"
          ]
        },
        "refusal": {
          "eyebrow": "THE COPILOT",
          "title": "It shows the arithmetic and says no",
          "lines": [
            "{{impossible_needed}} spaces needed, {{impossible_left}} allowed"
          ]
        },
        "pair": {
          "eyebrow": "SAME MODEL, SAME LAPTOP",
          "bars": [
            {
              "name": "Muse Glimmer 30B, without our fine-tune",
              "key": "reader_untrained_shown_pct",
              "label": "{{reader_untrained_shown_pct}} %"
            },
            {
              "name": "Muse Glimmer 30B, with our fine-tune",
              "key": "reader_exact_shown_pct",
              "label": "{{reader_exact_shown_pct}} %",
              "own": true
            }
          ],
          "bars_note": "{{reader_exact}} of {{reader_sealed_total}} locked test sentences with our fine-tune, {{reader_untrained_exact}} of {{reader_sealed_total}} without · not written by real planners",
          "big_head": "The mask overrode the model's first choice, before fine-tuning and after",
          "big": "{{xray_before_blocked}} of {{xray_study_sentences}} → {{xray_after_blocked}}",
          "lines": [
            "almost all were output format, not ship facts; none was an illegal bay or tier",
            "both of the {{xray_after_blocked}} after were sentences it should have refused"
          ]
        }
      },
      "shots": [
        {
          "id": 1,
          "title": "Cold open",
          "duration_s": 10,
          "still_t": 4.6,
          "v6": {
            "chapter": "voice",
            "from": 0,
            "to": 2.2,
            "fit_s": 2.2,
            "holes": [
              {
                "from": 2.2,
                "to": 3.2,
                "chapter": "reading",
                "start": 6.4,
                "rate": 1
              },
              {
                "from": 3.2,
                "to": 6.5,
                "chapter": "replan",
                "start": 1.3,
                "rate": 3,
                "speed": "cold_replan_speed"
              },
              {
                "from": 6.5,
                "to": 7.8,
                "chapter": "replan",
                "start": 15,
                "rate": 0
              },
              {
                "from": 7.8,
                "to": 9.2,
                "chapter": "breakit",
                "start": -3.5,
                "rate": 1.3
              },
              {
                "from": 9.2,
                "to": 10,
                "chapter": "breakit",
                "start": -1.68,
                "rate": 0
              }
            ]
          },
          "overlays": [
            {
              "from": 2.2,
              "to": 3.2,
              "type": "slam",
              "text": "ONE SENTENCE.",
              "fade": 0.02,
              "at": [
                1380,
                690
              ],
              "size": 118
            },
            {
              "from": 3.2,
              "to": 6.5,
              "type": "slam",
              "text": "THE PLAN CHANGES.",
              "fade": 0.02,
              "when": "hero_shown",
              "at": [
                715,
                335
              ],
              "size": 118
            },
            {
              "from": 6.5,
              "to": 7.8,
              "type": "slam",
              "text": "FULL CHECKER: PASSED",
              "fade": 0.02,
              "when": "hero_shown",
              "at": [
                715,
                335
              ],
              "size": 112
            },
            {
              "from": 9.2,
              "to": 10.0,
              "type": "slam",
              "text": "TRY TO BREAK IT.",
              "black": true,
              "fade": 0.02
            }
          ],
          "captions": [
            {
              "from": 0.2,
              "to": 2.1,
              "text": "Generated voice"
            },
            {
              "from": 2.25,
              "to": 3.15,
              "text": "{{reader_label}}"
            },
            {
              "from": 3.25,
              "to": 6.45,
              "text": "Stored plan, computed on {{change_plan_date}}, {{hero_recheck}} · sped up {{cold_replan_speed}}× · Half-ship section"
            },
            {
              "from": 6.55,
              "to": 7.75,
              "text": "full checker, {{hero_rules_active}} active rules"
            },
            {
              "from": 7.85,
              "to": 9.95,
              "text": "Recorded verdict · Separate demos"
            }
          ],
          "fallback": {
            "bleed": true,
            "plan": {
              "label": "v6:before"
            },
            "show_tag": true,
            "play": [
              [
                0,
                "end"
              ],
              [
                9,
                "end"
              ]
            ],
            "overlays": [
              {
                "from": 0,
                "to": 9,
                "type": "hits",
                "at": [
                  0.9,
                  1.9,
                  2.9
                ],
                "end_at": 5.2,
                "words": [
                  "TALLY SHEET",
                  "EMAIL",
                  "RADIO"
                ],
                "no_intake": [
                  "TYPED NOTE",
                  "SPREADSHEET",
                  "RADIO"
                ],
                "dare": "TRY TO BREAK IT",
                "no_breakit": "THEN TRY TO ARGUE WITH IT"
              }
            ],
            "captions": [
              {
                "from": 0.3,
                "to": 8.7,
                "text": "Synthetic document · Recorded · Separate demos"
              }
            ]
          },
          "show_tag": true
        },
        {
          "id": 2,
          "title": "The stake",
          "duration_s": 12,
          "bleed": true,
          "still_t": 3.5,
          "plan": {
            "label": "v6:before"
          },
          "show_tag": true,
          "play": [
            [
              0,
              0
            ],
            [
              8.7,
              0
            ],
            [
              11.8,
              "end"
            ]
          ],
          "camera": [
            {
              "t": 8.7,
              "zoom": 1.15,
              "focus": "active"
            },
            {
              "t": 12,
              "zoom": 1.5,
              "focus": "active"
            }
          ],
          "focus": [
            {
              "from": 5.4,
              "to": 8.7,
              "panel": "yard",
              "mode": "big",
              "rect": [
                300,
                230,
                1320,
                470
              ],
              "zoom": 1.5
            }
          ],
          "overlays": [
            {
              "from": 0,
              "to": 5.4,
              "type": "map"
            },
            {
              "from": 5.5,
              "to": 8.6,
              "type": "tag",
              "text": "simulated yard"
            },
            {
              "from": 8.8,
              "to": 11.8,
              "type": "tally",
              "label": "Synthetic document"
            },
            {
              "from": 8.8,
              "to": 11.8,
              "type": "footage",
              "text": "recorded footage goes here at 4.3"
            }
          ],
          "captions": [
            {
              "from": 0.5,
              "to": 5.3,
              "text": "{{stake_calls}} container-vessel calls in 2024: BSAA Annual Report 2024–25"
            }
          ]
        },
        {
          "id": 3,
          "title": "Mess in",
          "duration_s": 11,
          "still_t": 6,
          "v6": {
            "chapter": "intake",
            "from": 0,
            "at": 2.6,
            "hole": {
              "from": 0,
              "to": 2.6,
              "chapter": "hook",
              "start": 0.8,
              "rate": 1.2
            }
          },
          "captions": [
            {
              "from": 2.8,
              "to": 6.7,
              "text": "Synthetic document · Recorded answer · Muse Spark, cloud · {{intake_demo_boxes}} sample boxes, another voyage"
            },
            {
              "from": 6.8,
              "to": 10.7,
              "text": "development: Spark {{intake_dev_spark_pct}} % of fields, a parser with no model {{intake_dev_parser_pct}} %"
            }
          ],
          "fallback": {
            "bleed": true,
            "plan": {
              "label": "v6:before"
            },
            "show_tag": true,
            "play": [
              [
                0,
                "end"
              ],
              [
                11,
                "end"
              ]
            ],
            "overlays": [
              {
                "from": 0.2,
                "to": 10.8,
                "type": "rows",
                "title": "Sample load list",
                "count": 9
              }
            ],
            "captions": []
          }
        },
        {
          "id": 4,
          "title": "Bad radio line",
          "duration_s": 10,
          "still_t": 6,
          "v6": {
            "chapter": "voice",
            "from": 0
          },
          "captions": [
            {
              "from": 0.4,
              "to": 3.6,
              "text": "Stock text-to-speech voice · simulated radio: band-limited, {{radio_snr_db}} dB noise · Muse Voice Transcribe, cloud"
            },
            {
              "from": 3.7,
              "to": 6.6,
              "text": "Rule fixed before reading · {{radio_selected}} of {{radio_wordings}} wordings ({{radio_written}} written + the template)"
            },
            {
              "from": 6.7,
              "to": 9.7,
              "text": "on this simulated line, {{radio_lost_bay}} of {{radio_wordings}} transcripts garbled \"bay {{radio_bay}}\" · selected wording; counts, not a rate"
            }
          ],
          "fallback": {
            "duration_s": 6,
            "give": {
              "shot": 5,
              "s": 4
            },
            "bleed": true,
            "plan": {
              "label": "v6:before"
            },
            "show_tag": true,
            "play": [
              [
                0,
                0
              ],
              [
                6,
                0
              ]
            ],
            "overlays": [
              {
                "from": 0.2,
                "to": 5.8,
                "type": "card",
                "card": "typed"
              }
            ],
            "captions": []
          }
        },
        {
          "id": 5,
          "title": "The reader",
          "duration_s": 18,
          "still_t": 10,
          "v6": {
            "chapter": "reading",
            "from": 0,
            "to": 11.0
          },
          "captions": [
            {
              "from": 0.3,
              "to": 3.0,
              "text": "Live, on this laptop",
              "when": "reader_live",
              "otherwise": "{{reader_label}} · {{reader_adapter_name}} · each read once"
            },
            {
              "from": 3.1,
              "to": 7.0,
              "text": "all {{showcase_total}} prepared sentences in the recording: {{showcase_as_expected}} as expected, {{showcase_missed}} misread or refused, {{showcase_no_rule}} with no stored rule to compare ({{showcase_source}})"
            },
            {
              "from": 7.1,
              "to": 9.0,
              "text": "About {{reader_seconds_said}} s a sentence (warm median) · {{reader_peak_gb_said}} GB peak memory",
              "when": "reader_figures"
            },
            {
              "from": 9.1,
              "to": 12.4,
              "text": "{{reader_exact}} of {{reader_sealed_total}} locked test sentences, not written by real planners",
              "when": "reader_figures"
            },
            {
              "from": 12.5,
              "to": 15.0,
              "text": "missed: {{reader_loosening_said}} loosening request accepted, {{reader_spark_gap}} points below Muse Spark",
              "when": "reader_figures"
            },
            {
              "from": 15.1,
              "to": 17.7,
              "text": "built to refuse anything else; it can slip, so the planner confirms"
            }
          ],
          "fallback": {
            "bleed": true,
            "plan": {
              "label": "v6:before"
            },
            "show_tag": true,
            "play": [
              [
                0,
                0
              ],
              [
                18,
                0
              ]
            ],
            "overlays": [
              {
                "from": 0.2,
                "to": 17.8,
                "type": "card",
                "card": "reading"
              }
            ],
            "captions": []
          }
        },
        {
          "id": 6,
          "title": "X-ray and confirm",
          "duration_s": 16,
          "still_t": 6,
          "v6": {
            "chapter": "reading",
            "from": 0
          },
          "beats": [
            {
              "id": "6a",
              "from": 0,
              "to": 12.6
            },
            {
              "id": "6b",
              "from": 12.6,
              "to": 16
            }
          ],
          "overlays": [
            {
              "from": 0.1,
              "to": 12.6,
              "type": "pair",
              "card": "pair",
              "reveal": [
                0.3,
                3.2
              ]
            }
          ],
          "captions": [
            {
              "from": 3.3,
              "to": 7.9,
              "text": "{{xray_before_blocked}} of {{xray_study_sentences}} before fine-tuning, {{xray_after_blocked}} after · {{xray_study_sentences}} validation sentences, not the locked test"
            },
            {
              "from": 8.0,
              "to": 12.4,
              "text": "\"Before fine-tuning it can't even write the format; the mask spells it out.\""
            },
            {
              "from": 12.7,
              "to": 15.8,
              "text": "{{reader_label}}"
            }
          ],
          "fallback": {
            "bleed": true,
            "plan": {
              "label": "v6:before"
            },
            "show_tag": true,
            "play": [
              [
                0,
                0
              ],
              [
                16,
                0
              ]
            ],
            "overlays": [
              {
                "from": 0.1,
                "to": 10.8,
                "type": "card",
                "card": "mask"
              },
              {
                "from": 11.0,
                "to": 15.8,
                "type": "card",
                "card": "reading"
              }
            ],
            "captions": []
          }
        },
        {
          "id": 7,
          "title": "The plan changes",
          "duration_s": 14,
          "still_t": 6,
          "show_tag": true,
          "v6": {
            "chapter": "replan",
            "from": 0
          },
          "captions": [
            {
              "from": 0.3,
              "to": 4.4,
              "text": "Stored plan, computed on {{change_plan_date}}, {{hero_recheck}}"
            },
            {
              "from": 4.5,
              "to": 9.4,
              "text": "Engine {{change_engine}} plan, {{change_sequence}} · all {{change_frames}} crane frames walked and passed, peak heel {{change_peak_heel}}° of {{hero_heel_limit}}° at frame {{change_peak_frame}}",
              "when": "hero_walked"
            },
            {
              "from": 9.5,
              "to": 13.7,
              "text": "full checker, {{hero_rules_active}} active rules; lashing is our approximation"
            }
          ],
          "fallback": {
            "bleed": true,
            "plan": {
              "label": "v6:before"
            },
            "show_tag": true,
            "play": [
              [
                0,
                0
              ],
              [
                9,
                "end"
              ],
              [
                14,
                "end"
              ]
            ],
            "overlays": [
              {
                "from": 9.2,
                "to": 13.8,
                "type": "verdict",
                "stamp": "✓ FULL CHECKER PASSED"
              }
            ],
            "captions": []
          }
        },
        {
          "id": 8,
          "title": "Drag a box",
          "duration_s": 13,
          "still_t": 8,
          "show_tag": true,
          "v6": {
            "chapter": "breakit",
            "from": 0
          },
          "live_label": "Fast screen, full check running",
          "captions": [
            {
              "from": 0.4,
              "to": 6.4,
              "text": "Recorded verdict · Demo cargo declaration changed for this challenge; no vessel or rule limit tightened"
            },
            {
              "from": 6.5,
              "to": 12.7,
              "text": "one box, no replan · {{replay_drags}} pinned drags to try: {{replay_url}}"
            }
          ],
          "fallback": {
            "bleed": true,
            "plan": {
              "label": "v6:before"
            },
            "show_tag": true,
            "play": [
              [
                0,
                "end"
              ],
              [
                13,
                "end"
              ]
            ],
            "overlays": [
              {
                "from": 0.2,
                "to": 12.8,
                "type": "card",
                "card": "checker"
              }
            ],
            "captions": []
          }
        },
        {
          "id": 9,
          "title": "The copilot",
          "duration_s": 16,
          "still_t": 6,
          "show_tag": true,
          "v6": {
            "chapter": "copilot",
            "from": 4.6
          },
          "beats": [
            {
              "id": "9a",
              "from": 0,
              "to": 2.9
            },
            {
              "id": "9b",
              "from": 2.9,
              "to": 16
            }
          ],
          "captions": [
            {
              "from": 0.3,
              "to": 2.8,
              "text": "Stored refusal, proved by arithmetic · hero: in every bay at most {{impossible_tiers}} tier on deck · {{impossible_needed}} needed > {{impossible_left}} allowed, {{impossible_short}} short, in 20-foot spaces"
            },
            {
              "from": 3.0,
              "to": 10.4,
              "text": "Recorded answer · real tools · {{copilot_scripted_replies}} scripted planner replies · reader here is cloud Spark",
              "when": "copilot_delivered"
            },
            {
              "from": 10.5,
              "to": 15.7,
              "text": "we don't rank rules on a {{copilot_lift_gap}}-lift gap from one map",
              "when": "copilot_delivered"
            }
          ],
          "fallback": {
            "bleed": true,
            "plan": {
              "label": "v6:before"
            },
            "show_tag": true,
            "play": [
              [
                0,
                "end"
              ],
              [
                16,
                "end"
              ]
            ],
            "overlays": [
              {
                "from": 0.2,
                "to": 15.8,
                "type": "card",
                "card": "refusal"
              }
            ],
            "captions": []
          }
        },
        {
          "id": 10,
          "title": "Receipt",
          "duration_s": 14,
          "still_t": 8,
          "show_tag": true,
          "v6": {
            "chapter": "package",
            "from": 0
          },
          "captions": [
            {
              "from": 0.4,
              "to": 3.6,
              "text": "half-ship section · weights close to displacement · 3.1 on v3 ({{held_out_manifest}}); demo and package on v4 ({{package_manifest}})"
            },
            {
              "from": 3.7,
              "to": 6.6,
              "text": "live = when the package was built · It says what ran and that nothing has changed since. Our own signature."
            },
            {
              "from": 6.7,
              "to": 9.3,
              "text": "{{hero_rules_active}} active rules ran, {{hero_rules_off}} are off · [[IF thai-note: Thai note, reviewed for sense · ]]"
            },
            {
              "from": 9.4,
              "to": 13.7,
              "text": "Confirmed by: {{package_confirmed_by}}; the planner's tap earlier is the demo's confirmation · the bay plan and the note to the master print the confirmed instruction",
              "when": "package_batch_confirmed",
              "otherwise": "the bay plan and the note to the master print the confirmed instruction"
            }
          ],
          "fallback": {
            "duration_s": 9,
            "give": {
              "shot": 8,
              "s": 5
            },
            "bleed": true,
            "plan": {
              "label": "v6:before"
            },
            "show_tag": true,
            "play": [
              [
                0,
                "end"
              ],
              [
                9,
                "end"
              ]
            ],
            "focus": [
              {
                "from": 0,
                "to": 9,
                "panel": "rules",
                "mode": "big",
                "rect": [
                  560,
                  130,
                  800,
                  720
                ],
                "zoom": 1.3
              }
            ],
            "overlays": [],
            "captions": [
              {
                "from": 0.4,
                "to": 8.7,
                "text": "{{hero_rules_active}} active rules ran, {{hero_rules_off}} are off"
              }
            ]
          }
        },
        {
          "id": 11,
          "title": "Held-out test",
          "duration_s": 9,
          "bleed": true,
          "build_tag": "3.1 on v3 ({{held_out_manifest}}); demo and package on v4 ({{package_manifest}})",
          "still_t": 5,
          "plan": {
            "label": "v6:before"
          },
          "play": [
            [
              0,
              "end"
            ],
            [
              9,
              "end"
            ]
          ],
          "overlays": [
            {
              "from": 0.2,
              "to": 8.8,
              "type": "bars",
              "card": "sealed"
            }
          ],
          "captions": []
        },
        {
          "id": 12,
          "title": "Forward pilot",
          "duration_s": 24,
          "bleed": true,
          "still_t": 8,
          "plan": {
            "label": "v6:before"
          },
          "show_tag": true,
          "play": [
            [
              0,
              "end"
            ],
            [
              24,
              "end"
            ]
          ],
          "camera": [
            {
              "t": 0,
              "zoom": 1.25,
              "focus": "center"
            },
            {
              "t": 2,
              "zoom": 1.0,
              "focus": "center"
            }
          ],
          "overlays": [
            {
              "from": 0.1,
              "to": 17.2,
              "type": "chip",
              "text": "Projection"
            },
            {
              "from": 1.4,
              "to": 17.4,
              "type": "pilot",
              "card": "forward",
              "reveal": [
                2.0,
                7.0,
                10.6
              ]
            },
            {
              "from": 17.0,
              "to": 23.9,
              "type": "end",
              "layout": "full",
              "say": false,
              "tag": "Models propose. Software decides.",
              "voice_line": "Narration: generated voice",
              "lines": [
                "Three Meta models: Muse Glimmer 30B, fine-tuned by us as StowLM Reader, reads the planner's words on the laptop · Muse Spark reads paper and chooses tools · Muse Voice Transcribe hears the radio",
                "Plans by Frontier Engine {{change_engine}}, our search software. Every plan shown has been through the full checker; a plan that doesn't pass says which rules it breaks.",
                {
                  "links": "Try to break it: {{replay_url}} · Code: {{repo_url}}"
                },
                "Built 26 Oct – 4 Nov 2026 for the Meta hackathon · built for the Thai freight world my family works in",
                "Measured on synthetic ships and cargo at a simulated Laem Chabang-style port. Designed for up to 195 boxes in a half-ship section of feeder ships of 500 to 800 TEU, up to 70 % of that section, an empty section on a ship whose other bays hold fixed synthetic cargo, and one loading call. Research pilot, not for use on a real ship.",
                "Radio voice: stock text-to-speech, generated · Music: Sascha Ende, ende.app (CC BY 4.0) · Edit kit adapted from brag (latent-spaces, MIT)"
              ]
            }
          ],
          "captions": [
            {
              "from": 12.4,
              "to": 17.0,
              "text": "about ${{impact_port_musd}} million a year at Laem Chabang, by share of calls (projection; {{stake_calls}} of about {{impact_calls}} calls, {{impact_port_share_pct}} %)"
            }
          ]
        }
      ],
      "clip_x": {
        "spans": [
          {
            "shot": "1",
            "from": 1,
            "to": 8
          },
          {
            "shot": "8",
            "from": 1,
            "to": 9
          }
        ],
        "note": "15 s, 1080x1920: /stage?video=1&clip=x, shots 1 and 8 only"
      }
    }
  },
  "generated_voice": false,
  "hero_sentence": "Keep class 8 at least one bay from bay 1.",
  "hero_note": [
    "Keep class 8 at least one bay from bay 1.",
    "In bay 2, no more than 4 high on deck."
  ],
  "hero_plan": {
    "run": "hero-v5"
  },
  "hero_case": "hero",
  "standin_plan": {
    "run": "stowlm-instruction-binding",
    "instance": "random.small.29000201.0.7",
    "method": "stowlm"
  },
  "standin_case": "dg_keep_away-04",
  "standin_tag": "recorded stand-in · not the hero run",
  "hero_slot": "hero run pending (3.2)",
  "two_step_long": "a simple two-step planner, modelled from one field expert's description",
  "cards": {
    "readers": {
      "eyebrow": "SAME MODEL, SAME LAPTOP",
      "pending": "1.1",
      "scale_max": 100,
      "unit": " %",
      "title": "{{NUMBER: sealed sentences}} held-back sentences, read exactly",
      "rows": [
        {
          "label": "Muse Glimmer 30B, without our fine-tune",
          "key": "untrained Glimmer accuracy %",
          "at": 1.6
        },
        {
          "label": "Muse Glimmer 30B, with our fine-tune",
          "key": "reader accuracy %",
          "own": true,
          "at": 6.0
        },
        {
          "label": "Muse Spark, cloud, for reference",
          "key": "Spark accuracy %",
          "reference": true,
          "at": 10.0
        }
      ],
      "notes": [
        "exact match of the full rule",
        "sentences written by a separate AI reviewer, not real planners"
      ]
    },
    "chance": {
      "eyebrow": "TESTED AGAINST CHANCE",
      "title": "First plan clears the safety screen",
      "pending": "test v3",
      "scale_max": 100,
      "unit": " %",
      "rows": [
        {
          "label": "Fine-tuned planner",
          "key": "first-plan clear %: fine-tuned",
          "own": true,
          "at": 0.5
        },
        {
          "label": "Random moves under the same mask",
          "key": "first-plan clear %: random",
          "at": 1.5
        },
        {
          "label": "12-weight rule",
          "key": "first-plan clear %: 12-weight rule",
          "at": 2.5
        }
      ],
      "notes": []
    },
    "lifts": {
      "eyebrow": "EXTRA CRANE LIFTS · SMALL CALLS · SEALED TEST, RUN ONCE",
      "pending": "test v3",
      "headline": "{{NUMBER: lifts saved vs two-step %}} % fewer extra crane lifts than our two-step baseline",
      "rows": [
        {
          "label": "Our pipeline",
          "key": "pipeline mean lifts",
          "own": true,
          "at": 2.5
        },
        {
          "label": "Two-step baseline",
          "key": "two-step mean lifts",
          "at": 3.5
        }
      ],
      "limits": [
        "12–22 boxes",
        "2 ports",
        "2 hatch covers",
        "most of the saving is projected later-port lifts"
      ],
      "who": "Search software saves these, not the fine-tune.",
      "two_step": "Two-step baseline: a simple two-step planner, modelled from one field expert's description"
    },
    "worth": {
      "eyebrow": "LAEM CHABANG · ONE CALL · A SIZING, NOT A RESULT",
      "pending": "1.5",
      "headline": "Each 10 % of avoidable lifts ≈ ${{NUMBER: $ per call per 10 %, mid}}",
      "sub": "on an assumed 500-box call",
      "range": "low ${{NUMBER: $ per call per 10 %, low}} · high ${{NUMBER: $ per call per 10 %, high}}",
      "note": "assumptions, not a StowLM result"
    },
    "live": {
      "eyebrow": "LIVE, WI-FI OFF",
      "pending": "4.3 live take",
      "rows": [
        {
          "label": "Seconds to read",
          "key": "reader seconds per sentence",
          "unit": " s"
        },
        {
          "label": "Peak memory",
          "key": "reader peak memory GB",
          "unit": " GB"
        },
        {
          "label": "Adapter on open Muse Glimmer 30B",
          "key": "reader adapter MB",
          "unit": " MB"
        }
      ],
      "note": "Recorded live on the laptop at 4.3, with the Wi-Fi turned off on camera. Nothing on this card is staged."
    }
  },
  "shots": [
    {
      "id": 1,
      "title": "Hook",
      "duration_s": 10,
      "hero": true,
      "bleed": true,
      "still_t": 8,
      "play": [
        [
          0,
          4
        ],
        [
          5.0,
          4
        ],
        [
          9.8,
          "end"
        ]
      ],
      "camera": [
        {
          "t": 0,
          "zoom": 1.55,
          "focus": "active"
        },
        {
          "t": 10,
          "zoom": 1.95,
          "focus": "active"
        }
      ],
      "focus": [
        {
          "from": 5.0,
          "to": 10,
          "panel": "physics",
          "mode": "big",
          "cls": "dir-mini",
          "rect": [
            1390,
            716,
            486,
            168
          ]
        }
      ],
      "overlays": [
        {
          "from": 0,
          "to": 10,
          "type": "hook",
          "speak_from": 0.7,
          "speak_to": 3.2,
          "rule_at": 3.3,
          "confirm_at": 4.2,
          "black_until": 4.7
        },
        {
          "from": 5.0,
          "to": 9.9,
          "type": "baylight"
        },
        {
          "from": 5.0,
          "to": 9.9,
          "type": "tag",
          "text": "recorded, sped up"
        }
      ],
      "captions": [
        {
          "from": 5.1,
          "to": 9.8,
          "text": "Class 8: corrosive cargo · replan recorded, sped up; it took {{NUMBER: hero replan minutes}} min · a 12–22-box section of a ship · simulated port, synthetic ship"
        }
      ],
      "needs": [
        "3.2 hero plan",
        "hero replan minutes",
        "1.3 recorded voice"
      ]
    },
    {
      "id": 2,
      "title": "Why it matters",
      "duration_s": 21,
      "bleed": true,
      "still_t": 3.5,
      "plan": {
        "run": "showcase-v2-gold",
        "instance": "random.small.29000201.0.7",
        "method": "solver.gold_warmstart"
      },
      "play": [
        [
          0,
          0
        ],
        [
          6.0,
          0
        ],
        [
          20.6,
          "end"
        ]
      ],
      "camera": [
        {
          "t": 12.6,
          "zoom": 1.25,
          "focus": "active"
        },
        {
          "t": 21,
          "zoom": 1.8,
          "focus": "active"
        }
      ],
      "focus": [
        {
          "from": 5.8,
          "to": 12.6,
          "panel": "yard",
          "mode": "big",
          "rect": [
            300,
            230,
            1320,
            470
          ],
          "zoom": 1.5
        }
      ],
      "overlays": [
        {
          "from": 0,
          "to": 6.0,
          "type": "map"
        },
        {
          "from": 6.1,
          "to": 12.4,
          "type": "tag",
          "text": "simulated yard"
        },
        {
          "from": 12.7,
          "to": 20.8,
          "type": "tag",
          "text": "synthetic ship · recorded"
        }
      ],
      "captions": [
        {
          "from": 0.5,
          "to": 5.8,
          "text": "9,325 calls: BSAA Annual Report 2024–25"
        },
        {
          "from": 6.2,
          "to": 12.4,
          "text": "bay: one cross-section of the ship"
        },
        {
          "from": 12.8,
          "to": 20.8,
          "text": "feeder hire ≈ $15,700/day (Contships, Q4 2025 guidance)"
        }
      ],
      "needs": []
    },
    {
      "id": "3a",
      "title": "Say it, confirm it",
      "duration_s": 24,
      "hero": true,
      "bleed": true,
      "still_t": 14,
      "brief": [
        {
          "from": 0,
          "to": 7.0,
          "stage": "listening",
          "full": true,
          "progress_from": 1.0,
          "progress_to": 6.5
        },
        {
          "from": 7.0,
          "to": 10.0,
          "stage": "transcript",
          "full": true
        },
        {
          "from": 10.0,
          "to": 24,
          "stage": "reading",
          "full": true
        }
      ],
      "play": [
        [
          0,
          0
        ],
        [
          24,
          0
        ]
      ],
      "camera": [
        {
          "t": 0,
          "zoom": 1.0,
          "focus": "center"
        }
      ],
      "focus": [],
      "overlays": [
        {
          "from": 0,
          "to": 23.9,
          "type": "steps",
          "steps": [
            {
              "id": "speech",
              "label": "Speech to text",
              "from": 0,
              "to": 10.0
            },
            {
              "id": "reading",
              "label": "Reading",
              "from": 10.0,
              "to": 19.5
            },
            {
              "id": "confirm",
              "label": "Confirm",
              "from": 19.5,
              "to": 24
            }
          ]
        },
        {
          "from": 19.6,
          "to": 23.8,
          "type": "confirm"
        }
      ],
      "captions": [
        {
          "from": 0.5,
          "to": 6.9,
          "text": "Note: \"Keep class 8 at least one bay from bay 1.\" / \"In bay 2, no more than 4 high on deck.\"",
          "when": "hero"
        },
        {
          "from": 7.1,
          "to": 9.9,
          "text": "speech to text: Muse Voice Transcribe, cloud",
          "when": "hero"
        },
        {
          "from": 10.1,
          "to": 14.4,
          "text": "reading: Muse Glimmer 30B + our fine-tune, on device, {{NUMBER: reader seconds per sentence}} s, live",
          "when": "hero"
        },
        {
          "from": 14.5,
          "to": 19.4,
          "text": "four kinds: close a bay, cap a stack, keep dangerous goods away, one port per hatch cover"
        },
        {
          "from": 19.6,
          "to": 23.8,
          "text": "trained on {{NUMBER: reader training sentences}} sentence-to-rule pairs, no real data"
        }
      ],
      "needs": [
        "3.2 hero plan and note",
        "1.3 speech",
        "multi-line note in the brief flow",
        "per-line confirm in the brief flow",
        "reader seconds per sentence",
        "reader training sentences"
      ]
    },
    {
      "id": "3b",
      "title": "The ship moves",
      "duration_s": 16,
      "hero": true,
      "bleed": true,
      "still_t": 12,
      "brief": [
        {
          "from": 0,
          "to": 2.4,
          "stage": "searching"
        },
        {
          "from": 2.4,
          "to": 16,
          "stage": null
        }
      ],
      "play": [
        [
          0,
          0
        ],
        [
          2.4,
          0
        ],
        [
          8.2,
          "end"
        ],
        [
          16,
          "end"
        ]
      ],
      "camera": [
        {
          "t": 2.4,
          "zoom": 1.0,
          "focus": "center"
        },
        {
          "t": 3.6,
          "zoom": 1.5,
          "focus": "active"
        },
        {
          "t": 7.4,
          "zoom": 1.5,
          "focus": "active"
        },
        {
          "t": 8.6,
          "zoom": 1.0,
          "focus": "center"
        }
      ],
      "focus": [
        {
          "from": 8.4,
          "to": 16,
          "panel": "physics",
          "mode": "big",
          "rect": [
            1330,
            150,
            562,
            330
          ]
        },
        {
          "from": 8.4,
          "to": 16,
          "panel": "rules",
          "mode": "big",
          "rect": [
            1330,
            490,
            562,
            370
          ]
        }
      ],
      "rules_reveal": {
        "from": 8.6,
        "per_family_s": 0.6
      },
      "overlays": [
        {
          "from": 0,
          "to": 15.9,
          "type": "steps",
          "steps": [
            {
              "id": "plan",
              "label": "Plan",
              "from": 0,
              "to": 8.4
            },
            {
              "id": "check",
              "label": "Check",
              "from": 8.4,
              "to": 16
            }
          ]
        },
        {
          "from": 2.5,
          "to": 15.8,
          "type": "tag",
          "text": "recorded plan"
        },
        {
          "from": 8.5,
          "to": 15.8,
          "type": "diff"
        }
      ],
      "captions": [
        {
          "from": 2.6,
          "to": 8.2,
          "text": "Plan: search software, recorded · lashing: the rods and locks that hold boxes down",
          "when": "hero"
        },
        {
          "from": 8.6,
          "to": 15.8,
          "text": "{{NUMBER: hero lifts before}} → {{NUMBER: hero lifts after}} lifts · {{NUMBER: hero boxes moved}} boxes moved · checker passed",
          "when": "hero"
        }
      ],
      "needs": [
        "3.2 hero plan and its before plan",
        "hero lifts before",
        "hero lifts after",
        "hero extra lifts",
        "hero boxes moved"
      ]
    },
    {
      "id": "3c",
      "title": "Is it really here?",
      "duration_s": 11,
      "bleed": true,
      "still_t": 6,
      "plan": {
        "run": "showcase-v2-gold",
        "instance": "random.small.29000201.0.7",
        "method": "solver.gold_warmstart"
      },
      "play": [
        [
          0,
          "end"
        ],
        [
          11,
          "end"
        ]
      ],
      "overlays": [
        {
          "from": 0.2,
          "to": 10.8,
          "type": "live",
          "card": "live"
        }
      ],
      "captions": [
        {
          "from": 0.5,
          "to": 5.4,
          "text": "Live, Wi-Fi off · {{NUMBER: reader seconds per sentence}} s · {{NUMBER: reader peak memory GB}} GB"
        },
        {
          "from": 5.5,
          "to": 10.7,
          "text": "{{NUMBER: reader adapter MB}} MB adapter on open Muse Glimmer 30B · adapter hash shown · adapter and one-line command in the repo"
        }
      ],
      "needs": [
        "4.3 live capture with the Wi-Fi off",
        "5.2 published adapter",
        "reader seconds per sentence",
        "reader peak memory GB",
        "reader adapter MB"
      ]
    },
    {
      "id": 4,
      "title": "Spark says why",
      "duration_s": 9,
      "hero": true,
      "standin": "plan",
      "bleed": true,
      "still_t": 6,
      "play": [
        [
          0,
          "end"
        ],
        [
          9,
          "end"
        ]
      ],
      "overlays": [
        {
          "from": 0.2,
          "to": 8.8,
          "type": "spark_lines",
          "lit_from": 0.9,
          "lit_to": 8.5
        }
      ],
      "captions": [
        {
          "from": 0.5,
          "to": 8.6,
          "text": "Muse Spark, Meta Model API · every statement comes from this plan's checker report",
          "when": "spark_explained"
        }
      ],
      "needs": [
        "3.2 explanation bound to the hero plan"
      ]
    },
    {
      "id": 5,
      "title": "When it says no",
      "duration_s": 17,
      "bleed": true,
      "still_t": 3,
      "plan": {
        "run": "showcase-v2-gold",
        "instance": "random.small.29000201.0.7",
        "method": "solver.gold_warmstart"
      },
      "play": [
        [
          0,
          "end"
        ],
        [
          17,
          "end"
        ]
      ],
      "beats": [
        {
          "id": "5a",
          "from": 0,
          "to": 6
        },
        {
          "id": "5b",
          "from": 6,
          "to": 10
        },
        {
          "id": "5c",
          "from": 10,
          "to": 17
        }
      ],
      "brief": [
        {
          "from": 0,
          "to": 6,
          "stage": "refused",
          "full": true,
          "sentence": "Ignore the dangerous-goods segregation rules."
        },
        {
          "from": 6,
          "to": 10,
          "stage": "refused",
          "full": true,
          "sentence": "Keep the dangerous cargo farther away."
        },
        {
          "from": 10,
          "to": 17,
          "stage": "impossible",
          "full": true,
          "sentence": "Across the vessel, use at most zero occupied tiers per deck stack."
        }
      ],
      "overlays": [],
      "captions": [
        {
          "from": 0.8,
          "to": 5.8,
          "text": "Loosening requests accepted: {{NUMBER: loosening requests accepted}} of {{NUMBER: loosening requests tested}}"
        },
        {
          "from": 10.4,
          "to": 13.6,
          "text": "no boxes on deck, this section: {{NUMBER: impossible case space needed}} 20-foot spaces needed, {{NUMBER: impossible case space left}} left"
        },
        {
          "from": 13.7,
          "to": 16.8,
          "text": "alternative: at most {{NUMBER: alternative stack cap}} high; a stored plan passed the checker, no guarantee for a new one"
        }
      ],
      "needs": [
        "1.4 cards rerecorded through the final reader",
        "loosening requests accepted",
        "loosening requests tested",
        "impossible case space needed",
        "impossible case space left",
        "alternative stack cap"
      ]
    },
    {
      "id": 6,
      "title": "Does it work?",
      "duration_s": 18,
      "bleed": true,
      "still_t": 13,
      "plan": {
        "run": "showcase-v2-gold",
        "instance": "random.small.29000201.0.7",
        "method": "solver.gold_warmstart"
      },
      "play": [
        [
          0,
          "end"
        ],
        [
          18,
          "end"
        ]
      ],
      "overlays": [
        {
          "from": 0.3,
          "to": 17.8,
          "type": "bars",
          "card": "readers"
        }
      ],
      "captions": [
        {
          "from": 0.6,
          "to": 5.2,
          "text": "exact match of the full rule · sentences written by a separate AI reviewer, not real planners"
        },
        {
          "from": 5.3,
          "to": 9.6,
          "text": "silent wrong rules: {{NUMBER: reader silent wrong rules}}; safe refusals of good sentences: {{NUMBER: reader safe refusals of good sentences}}"
        },
        {
          "from": 9.7,
          "to": 13.8,
          "text": "our first reader failed its sealed set, so that set was retired and a new one written"
        },
        {
          "from": 13.9,
          "to": 17.7,
          "text": "Where it read an instruction right, the plan obeyed on {{NUMBER: feasible-cohort plans that obeyed}} of {{NUMBER: feasible-cohort reader exact}} maps"
        }
      ],
      "needs": [
        "1.1 reader numbers",
        "0.4 feasible cohort"
      ]
    },
    {
      "id": 7,
      "title": "What we tested",
      "duration_s": 13,
      "bleed": true,
      "still_t": 2,
      "plan": {
        "run": "stowlm-showcase",
        "instance": "random.small.29000201.0.7",
        "method": "stowlm"
      },
      "play": [
        [
          0,
          "xray"
        ],
        [
          13,
          "xray"
        ]
      ],
      "xray": [
        {
          "from": 0,
          "to": 3.4,
          "hl": false
        }
      ],
      "camera": [
        {
          "t": 0,
          "zoom": 1.45,
          "focus": "xray"
        }
      ],
      "focus": [],
      "overlays": [
        {
          "from": 0.2,
          "to": 3.3,
          "type": "override"
        },
        {
          "from": 3.4,
          "to": 12.8,
          "type": "bars",
          "card": "chance",
          "offset": 3.4
        }
      ],
      "captions": [
        {
          "from": 0.3,
          "to": 3.3,
          "extra": true,
          "text": "Mask x-ray of a recorded step: legal slots green, forbidden slots hatched red"
        },
        {
          "from": 3.5,
          "to": 12.7,
          "text": "First plan clears the safety screen: fine-tuned {{NUMBER: first-plan clear %: fine-tuned}} %, 12-weight rule {{NUMBER: first-plan clear %: 12-weight rule}} %, random {{NUMBER: first-plan clear %: random}} % · lifts vs random: {{NUMBER: lifts vs random %}} %"
        }
      ],
      "needs": [
        "3.1 test v3 chance arms"
      ]
    },
    {
      "id": 8,
      "title": "What it's worth",
      "duration_s": 25,
      "bleed": true,
      "still_t": 10,
      "plan": {
        "run": "showcase-v2-gold",
        "instance": "random.small.29000201.0.7",
        "method": "solver.gold_warmstart"
      },
      "play": [
        [
          0,
          "end"
        ],
        [
          25,
          "end"
        ]
      ],
      "overlays": [
        {
          "from": 0.3,
          "to": 24.8,
          "type": "lifts",
          "card": "lifts"
        },
        {
          "from": 15.0,
          "to": 24.8,
          "type": "worth",
          "card": "worth"
        }
      ],
      "captions": [
        {
          "from": 0.6,
          "to": 5.8,
          "text": "{{NUMBER: pipeline mean lifts}} vs {{NUMBER: two-step mean lifts}} extra lifts on {{NUMBER: maps both pass}} maps both pass · passes {{NUMBER: pipeline passes}} vs {{NUMBER: two-step passes}} of {{NUMBER: test maps}}"
        },
        {
          "from": 5.9,
          "to": 10.2,
          "text": "Navis, a commercial planning tool, and human planners: unknown"
        },
        {
          "from": 10.3,
          "to": 14.9,
          "text": "later ports: {{NUMBER: share of saving at later ports %}} % of the saving · hatch cover: the steel lid over a hold"
        },
        {
          "from": 15.1,
          "to": 24.7,
          "text": "sizing on an assumed 500-box call: each 10 % of avoidable lifts ≈ ${{NUMBER: $ per call per 10 %, mid}}, low {{NUMBER: $ per call per 10 %, low}}, high {{NUMBER: $ per call per 10 %, high}}; assumptions, not a StowLM result"
        }
      ],
      "needs": [
        "3.1 test v3",
        "1.5 impact rebase"
      ]
    },
    {
      "id": 9,
      "title": "End card",
      "duration_s": 10,
      "hero": true,
      "bleed": true,
      "still_t": 6,
      "play": [
        [
          0,
          "end"
        ],
        [
          10,
          "end"
        ]
      ],
      "camera": [
        {
          "t": 0,
          "zoom": 1.0,
          "focus": "center"
        }
      ],
      "focus": [
        {
          "from": 0,
          "to": 10,
          "panel": "physics",
          "mode": "big",
          "cls": "dir-mini",
          "rect": [
            1400,
            34,
            486,
            168
          ]
        }
      ],
      "overlays": [
        {
          "from": 0.3,
          "to": 9.9,
          "type": "end",
          "lines": [
            "Built 26 Oct – 4 Nov 2026 · {{REPO_URL}}",
            "Measured on a simulated Laem Chabang-style port with synthetic feeder ships. Not a class-approved loading computer."
          ]
        }
      ],
      "captions": [],
      "needs": [
        "REPO_URL",
        "3.2 hero plan",
        "0.10"
      ]
    }
  ],
  "clip_x": {
    "spans": [
      {
        "shot": "1",
        "from": 1.5,
        "to": 7.5
      },
      {
        "shot": "3a",
        "from": 10,
        "to": 13
      },
      {
        "shot": "3b",
        "from": 4,
        "to": 7
      },
      {
        "shot": "5a",
        "from": 1,
        "to": 4
      }
    ],
    "note": "15 s, 1080x1920 portrait layout of the same shots: /stage?video=1&clip=x"
  }
};
