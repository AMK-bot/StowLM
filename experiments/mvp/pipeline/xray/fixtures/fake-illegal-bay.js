window.XRAY_FIXTURE = {
  "format": "xray.trace/1",
  "source": {
    "kind": "fake",
    "label": "FAKE — scripted model, not Glimmer",
    "synthetic": true
  },
  "sentence": "Close bay 7.",
  "context": {
    "bays": [
      3,
      7,
      11
    ],
    "max_tiers": {
      "hold": 4,
      "deck": 3
    },
    "has_accommodation": true,
    "hatch_covers": [
      "h1"
    ]
  },
  "decoding_policy": "reader-v3-schema-mask/2-d224",
  "probability_basis": "full-vocabulary softmax of raw logits before the mask",
  "reason_basis": "inferred by whole-token counterfactual grammar; compound failures use not_in_grammar",
  "top_k": 6,
  "steps": [
    {
      "step": 1,
      "top_k": [
        {
          "token_id": 0,
          "token": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":",
          "probability": 0.8776850752471247,
          "blocked": false,
          "reason": null
        },
        {
          "token_id": 12,
          "token": "x",
          "probability": 0.11878175825111735,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 1,
          "token": "{\"type\":",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 2,
          "token": "\"close_bay\"",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 3,
          "token": ",\"bay\":",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 4,
          "token": "7",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        }
      ],
      "chosen": {
        "token_id": 0,
        "token": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":",
        "probability": 0.8776850752471247
      },
      "json_so_far": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":",
      "is_stop": false
    },
    {
      "step": 2,
      "top_k": [
        {
          "token_id": 1,
          "token": "{\"type\":",
          "probability": 0.8776850752471247,
          "blocked": false,
          "reason": null
        },
        {
          "token_id": 12,
          "token": "x",
          "probability": 0.11878175825111735,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 0,
          "token": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 2,
          "token": "\"close_bay\"",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 3,
          "token": ",\"bay\":",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 4,
          "token": "7",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        }
      ],
      "chosen": {
        "token_id": 1,
        "token": "{\"type\":",
        "probability": 0.8776850752471247
      },
      "json_so_far": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":{\"type\":",
      "is_stop": false
    },
    {
      "step": 3,
      "top_k": [
        {
          "token_id": 2,
          "token": "\"close_bay\"",
          "probability": 0.8776850752471247,
          "blocked": false,
          "reason": null
        },
        {
          "token_id": 12,
          "token": "x",
          "probability": 0.11878175825111735,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 0,
          "token": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 1,
          "token": "{\"type\":",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 3,
          "token": ",\"bay\":",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 4,
          "token": "7",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        }
      ],
      "chosen": {
        "token_id": 2,
        "token": "\"close_bay\"",
        "probability": 0.8776850752471247
      },
      "json_so_far": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":{\"type\":\"close_bay\"",
      "is_stop": false
    },
    {
      "step": 4,
      "top_k": [
        {
          "token_id": 3,
          "token": ",\"bay\":",
          "probability": 0.8776850752471247,
          "blocked": false,
          "reason": null
        },
        {
          "token_id": 12,
          "token": "x",
          "probability": 0.11878175825111735,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 0,
          "token": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 1,
          "token": "{\"type\":",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 2,
          "token": "\"close_bay\"",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 4,
          "token": "7",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        }
      ],
      "chosen": {
        "token_id": 3,
        "token": ",\"bay\":",
        "probability": 0.8776850752471247
      },
      "json_so_far": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":{\"type\":\"close_bay\",\"bay\":",
      "is_stop": false
    },
    {
      "step": 5,
      "top_k": [
        {
          "token_id": 7,
          "token": "99}}",
          "probability": 0.6428652895319883,
          "blocked": true,
          "reason": "bay_not_on_ship"
        },
        {
          "token_id": 6,
          "token": "99",
          "probability": 0.23649692346154536,
          "blocked": true,
          "reason": "bay_not_on_ship"
        },
        {
          "token_id": 8,
          "token": "7x",
          "probability": 0.08700235604179866,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 4,
          "token": "7",
          "probability": 0.03200637812125575,
          "blocked": false,
          "reason": null
        },
        {
          "token_id": 12,
          "token": "x",
          "probability": 0.0015935037357306648,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 0,
          "token": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":",
          "probability": 3.949900853468359e-06,
          "blocked": true,
          "reason": "not_in_grammar"
        }
      ],
      "chosen": {
        "token_id": 4,
        "token": "7",
        "probability": 0.03200637812125575
      },
      "json_so_far": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":{\"type\":\"close_bay\",\"bay\":7",
      "is_stop": false
    },
    {
      "step": 6,
      "top_k": [
        {
          "token_id": 5,
          "token": "}}",
          "probability": 0.8776850752471247,
          "blocked": false,
          "reason": null
        },
        {
          "token_id": 12,
          "token": "x",
          "probability": 0.11878175825111735,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 0,
          "token": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 1,
          "token": "{\"type\":",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 2,
          "token": "\"close_bay\"",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 3,
          "token": ",\"bay\":",
          "probability": 0.0002944305418132143,
          "blocked": true,
          "reason": "not_in_grammar"
        }
      ],
      "chosen": {
        "token_id": 5,
        "token": "}}",
        "probability": 0.8776850752471247
      },
      "json_so_far": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":{\"type\":\"close_bay\",\"bay\":7}}",
      "is_stop": false
    },
    {
      "step": 7,
      "top_k": [
        {
          "token_id": 13,
          "token": "<eos>",
          "probability": 0.8776850752471242,
          "blocked": false,
          "reason": null
        },
        {
          "token_id": 12,
          "token": "x",
          "probability": 0.11878175825111728,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 0,
          "token": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":",
          "probability": 0.0002944305418132141,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 1,
          "token": "{\"type\":",
          "probability": 0.0002944305418132141,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 2,
          "token": "\"close_bay\"",
          "probability": 0.0002944305418132141,
          "blocked": true,
          "reason": "not_in_grammar"
        },
        {
          "token_id": 3,
          "token": ",\"bay\":",
          "probability": 0.0002944305418132141,
          "blocked": true,
          "reason": "not_in_grammar"
        }
      ],
      "chosen": {
        "token_id": 13,
        "token": "<eos>",
        "probability": 0.8776850752471242
      },
      "json_so_far": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":{\"type\":\"close_bay\",\"bay\":7}}",
      "is_stop": true
    }
  ],
  "output": "{\"schema_version\":\"stowlm.reader/2\",\"constraint\":{\"type\":\"close_bay\",\"bay\":7}}",
  "complete": true,
  "stage_record": {
    "stage": "reader_xray",
    "mode": "generated",
    "input_sha256": "ba9608935933d1ef2b4c157df826136c5785ae0fd2941a93c92ec57333e98e60",
    "output_sha256": "6246d7d892cdc75b019e9ef7d42bde33238e681abaf3a97c339f9201e42834ff",
    "model": "FAKE-scripted-logits",
    "adapter_sha256": null,
    "prompt_sha256": null,
    "source_sha256": "db55700d4de5562a0a68aa7619c00c4f8ac6b1ab7c87c90544c71d4e501bdb03",
    "wall_s": 0.03181358298752457,
    "cost_usd": 0.0,
    "notes": "FAKE scripted logits; no model inference",
    "prev_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "payload_json": "{\"complete\":true,\"context\":{\"bays\":[3,7,11],\"has_accommodation\":true,\"hatch_covers\":[\"h1\"],\"max_tiers\":{\"deck\":3,\"hold\":4}},\"decoding_policy\":\"reader-v3-schema-mask/2-d224\",\"format\":\"xray.trace/1\",\"output\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":{\\\"type\\\":\\\"close_bay\\\",\\\"bay\\\":7}}\",\"probability_basis\":\"full-vocabulary softmax of raw logits before the mask\",\"reason_basis\":\"inferred by whole-token counterfactual grammar; compound failures use not_in_grammar\",\"sentence\":\"Close bay 7.\",\"source\":{\"kind\":\"fake\",\"label\":\"FAKE — scripted model, not Glimmer\",\"synthetic\":true},\"stage_record\":{\"adapter_sha256\":null,\"cost_usd\":0.0,\"input_sha256\":\"ba9608935933d1ef2b4c157df826136c5785ae0fd2941a93c92ec57333e98e60\",\"mode\":\"generated\",\"model\":\"FAKE-scripted-logits\",\"notes\":\"FAKE scripted logits; no model inference\",\"prev_sha256\":\"0000000000000000000000000000000000000000000000000000000000000000\",\"prompt_sha256\":null,\"source_sha256\":\"db55700d4de5562a0a68aa7619c00c4f8ac6b1ab7c87c90544c71d4e501bdb03\",\"stage\":\"reader_xray\",\"wall_s\":0.03181358298752457},\"steps\":[{\"chosen\":{\"probability\":0.8776850752471247,\"token\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":\",\"token_id\":0},\"is_stop\":false,\"json_so_far\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":\",\"step\":1,\"top_k\":[{\"blocked\":false,\"probability\":0.8776850752471247,\"reason\":null,\"token\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":\",\"token_id\":0},{\"blocked\":true,\"probability\":0.11878175825111735,\"reason\":\"not_in_grammar\",\"token\":\"x\",\"token_id\":12},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"{\\\"type\\\":\",\"token_id\":1},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"\\\"close_bay\\\"\",\"token_id\":2},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\",\\\"bay\\\":\",\"token_id\":3},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"7\",\"token_id\":4}]},{\"chosen\":{\"probability\":0.8776850752471247,\"token\":\"{\\\"type\\\":\",\"token_id\":1},\"is_stop\":false,\"json_so_far\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":{\\\"type\\\":\",\"step\":2,\"top_k\":[{\"blocked\":false,\"probability\":0.8776850752471247,\"reason\":null,\"token\":\"{\\\"type\\\":\",\"token_id\":1},{\"blocked\":true,\"probability\":0.11878175825111735,\"reason\":\"not_in_grammar\",\"token\":\"x\",\"token_id\":12},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":\",\"token_id\":0},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"\\\"close_bay\\\"\",\"token_id\":2},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\",\\\"bay\\\":\",\"token_id\":3},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"7\",\"token_id\":4}]},{\"chosen\":{\"probability\":0.8776850752471247,\"token\":\"\\\"close_bay\\\"\",\"token_id\":2},\"is_stop\":false,\"json_so_far\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":{\\\"type\\\":\\\"close_bay\\\"\",\"step\":3,\"top_k\":[{\"blocked\":false,\"probability\":0.8776850752471247,\"reason\":null,\"token\":\"\\\"close_bay\\\"\",\"token_id\":2},{\"blocked\":true,\"probability\":0.11878175825111735,\"reason\":\"not_in_grammar\",\"token\":\"x\",\"token_id\":12},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":\",\"token_id\":0},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"{\\\"type\\\":\",\"token_id\":1},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\",\\\"bay\\\":\",\"token_id\":3},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"7\",\"token_id\":4}]},{\"chosen\":{\"probability\":0.8776850752471247,\"token\":\",\\\"bay\\\":\",\"token_id\":3},\"is_stop\":false,\"json_so_far\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":{\\\"type\\\":\\\"close_bay\\\",\\\"bay\\\":\",\"step\":4,\"top_k\":[{\"blocked\":false,\"probability\":0.8776850752471247,\"reason\":null,\"token\":\",\\\"bay\\\":\",\"token_id\":3},{\"blocked\":true,\"probability\":0.11878175825111735,\"reason\":\"not_in_grammar\",\"token\":\"x\",\"token_id\":12},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":\",\"token_id\":0},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"{\\\"type\\\":\",\"token_id\":1},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"\\\"close_bay\\\"\",\"token_id\":2},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"7\",\"token_id\":4}]},{\"chosen\":{\"probability\":0.03200637812125575,\"token\":\"7\",\"token_id\":4},\"is_stop\":false,\"json_so_far\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":{\\\"type\\\":\\\"close_bay\\\",\\\"bay\\\":7\",\"step\":5,\"top_k\":[{\"blocked\":true,\"probability\":0.6428652895319883,\"reason\":\"bay_not_on_ship\",\"token\":\"99}}\",\"token_id\":7},{\"blocked\":true,\"probability\":0.23649692346154536,\"reason\":\"bay_not_on_ship\",\"token\":\"99\",\"token_id\":6},{\"blocked\":true,\"probability\":0.08700235604179866,\"reason\":\"not_in_grammar\",\"token\":\"7x\",\"token_id\":8},{\"blocked\":false,\"probability\":0.03200637812125575,\"reason\":null,\"token\":\"7\",\"token_id\":4},{\"blocked\":true,\"probability\":0.0015935037357306648,\"reason\":\"not_in_grammar\",\"token\":\"x\",\"token_id\":12},{\"blocked\":true,\"probability\":3.949900853468359e-06,\"reason\":\"not_in_grammar\",\"token\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":\",\"token_id\":0}]},{\"chosen\":{\"probability\":0.8776850752471247,\"token\":\"}}\",\"token_id\":5},\"is_stop\":false,\"json_so_far\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":{\\\"type\\\":\\\"close_bay\\\",\\\"bay\\\":7}}\",\"step\":6,\"top_k\":[{\"blocked\":false,\"probability\":0.8776850752471247,\"reason\":null,\"token\":\"}}\",\"token_id\":5},{\"blocked\":true,\"probability\":0.11878175825111735,\"reason\":\"not_in_grammar\",\"token\":\"x\",\"token_id\":12},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":\",\"token_id\":0},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"{\\\"type\\\":\",\"token_id\":1},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\"\\\"close_bay\\\"\",\"token_id\":2},{\"blocked\":true,\"probability\":0.0002944305418132143,\"reason\":\"not_in_grammar\",\"token\":\",\\\"bay\\\":\",\"token_id\":3}]},{\"chosen\":{\"probability\":0.8776850752471242,\"token\":\"<eos>\",\"token_id\":13},\"is_stop\":true,\"json_so_far\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":{\\\"type\\\":\\\"close_bay\\\",\\\"bay\\\":7}}\",\"step\":7,\"top_k\":[{\"blocked\":false,\"probability\":0.8776850752471242,\"reason\":null,\"token\":\"<eos>\",\"token_id\":13},{\"blocked\":true,\"probability\":0.11878175825111728,\"reason\":\"not_in_grammar\",\"token\":\"x\",\"token_id\":12},{\"blocked\":true,\"probability\":0.0002944305418132141,\"reason\":\"not_in_grammar\",\"token\":\"{\\\"schema_version\\\":\\\"stowlm.reader/2\\\",\\\"constraint\\\":\",\"token_id\":0},{\"blocked\":true,\"probability\":0.0002944305418132141,\"reason\":\"not_in_grammar\",\"token\":\"{\\\"type\\\":\",\"token_id\":1},{\"blocked\":true,\"probability\":0.0002944305418132141,\"reason\":\"not_in_grammar\",\"token\":\"\\\"close_bay\\\"\",\"token_id\":2},{\"blocked\":true,\"probability\":0.0002944305418132141,\"reason\":\"not_in_grammar\",\"token\":\",\\\"bay\\\":\",\"token_id\":3}]}],\"top_k\":6}"
};
