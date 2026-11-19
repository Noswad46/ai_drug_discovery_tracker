/* AI-designed drugs in the clinic — dashboard.
   Everything is rendered from window.DASHBOARD (site/data/dashboard.js),
   which scripts/build.py regenerates after every refresh. */

(function () {
  "use strict";

  var D = window.DASHBOARD;
  var root = document.getElementById("app");
  if (!D) {
    root.innerHTML =
      '<p class="empty">No data loaded. Run <code>python3 scripts/refresh.py</code> then ' +
      "<code>python3 scripts/build.py</code>.</p>";
    return;
  }

  var STAGES = D.meta.funnel_stages || ["Phase 1", "Phase 2", "Phase 3", "Filed", "Approved"];
  var STAGE_COLOR = {
    "Phase 1": "var(--phase-1)",
    "Phase 2": "var(--phase-2)",
    "Phase 3": "var(--phase-3)",
    Filed: "var(--phase-4)",
    Approved: "var(--phase-5)"
  };
  var STATUS_LABEL = {
    active: "Active",
    approved: "Approved",
    completed: "Completed",
    deprioritised: "Deprioritised",
    discontinued: "Discontinued",
    unclear: "Unclear"
  };
  // The core claim of every row: how AI was actually used. Fixed slot order,
  // never assigned by rank, so a filtered view never repaints the survivors.
  var AI_ROLE = {
    molecule_design: { label: "Molecule design", color: "var(--series-1)" },
    target_id: { label: "Target ID", color: "var(--series-2)" },
    both: { label: "Target + molecule", color: "var(--series-3)" },
    repurposing: { label: "Repurposing", color: "var(--series-4)" },
    personalised_design: { label: "Personalised design", color: "var(--series-5)" },
    other: { label: "Other", color: "var(--muted)" }
  };

  var CHANGE_LABEL = {
    drug_added: "new drug",
    drug_removed: "removed",
    phase_change: "phase",
    status_change: "status",
    trial_added: "new trial",
    trial_status_change: "trial status",
    results_posted: "results",
    enrolment_change: "enrolment",
    candidate_flagged: "for review"
  };

  // ---------------------------------------------------------------- utils

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function num(value) {
    return value == null ? "—" : Number(value).toLocaleString("en-GB");
  }

  function uniq(list) {
    return list.filter(function (item, index) {
      return item && list.indexOf(item) === index;
    });
  }

  function sortedValues(list) {
    return uniq(list).sort(function (a, b) {
      return a.localeCompare(b);
    });
  }

  function titleCase(value) {
    if (!value) return "";
    if (value === "CNS") return value;
    return value.charAt(0).toUpperCase() + value.slice(1);
  }

  // Let long labels wrap at a slash or colon instead of mid-word.
  function softBreak(value) {
    return String(value || "").replace(/([\/:])/g, "$1\u200b");
  }

  function area(drug) {
    return drug.therapy_area_group || drug.therapy_area || "other";
  }

  function roleOf(drug) {
    return AI_ROLE[drug.ai_role_group] ? drug.ai_role_group : "other";
  }

  // `Pharma.AI (PandaOmics, Chemistry42)` and `Pharma.AI (Chemistry42)` are the
  // same platform spelled two ways; the filter needs one entry, not forty.
  function platformFamily(drug) {
    var raw = (drug.ai_platform || "").replace(/\(.*?\)/g, " ");
    raw = raw.replace(/\s+/g, " ").trim().replace(/[,;\/]+$/, "");
    return raw || "not stated";
  }

  function prettyDate(value) {
    if (!value) return "—";
    var parts = String(value).split("-");
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    if (parts.length === 1) return parts[0];
    var month = months[parseInt(parts[1], 10) - 1] || "";
    return parts.length >= 3 ? parts[2].replace(/^0/, "") + " " + month + " " + parts[0] : month + " " + parts[0];
  }

  function stageIndex(stage) {
    return STAGES.indexOf(stage);
  }

  function drugStage(drug) {
    return (drug.derived && drug.derived.phase_bucket) || null;
  }

  function ctgStatusLabel(value) {
    return value ? value.replace(/_/g, " ").toLowerCase() : "unknown";
  }

  // --------------------------------------------------------------- tooltip

  var tip = document.createElement("div");
  tip.className = "tooltip";
  tip.setAttribute("role", "status");
  document.body.appendChild(tip);

  function showTip(event, html) {
    tip.innerHTML = html;
    tip.classList.add("on");
    moveTip(event);
  }
  function moveTip(event) {
    var pad = 14;
    var box = tip.getBoundingClientRect();
    var x = Math.min(event.clientX + pad, window.innerWidth - box.width - 8);
    var y = event.clientY + pad;
    if (y + box.height > window.innerHeight - 8) y = event.clientY - box.height - pad;
    tip.style.left = Math.max(8, x) + "px";
    tip.style.top = Math.max(8, y) + "px";
  }
  function hideTip() {
    tip.classList.remove("on");
  }
  function bindTip(element, html) {
    element.addEventListener("mouseenter", function (e) {
      showTip(e, html);
    });
    element.addEventListener("mousemove", moveTip);
    element.addEventListener("mouseleave", hideTip);
  }

  // ----------------------------------------------------------------- state

  var state = {
    q: "",
    area: "",
    modality: "",
    company: "",
    role: "",
    platform: "",
    stage: "",
    status: "",
    confidence: "",
    strict: false,
    sort: { key: "phase", dir: "desc" },
    open: {}
  };

  function readHash() {
    var hash = location.hash.replace(/^#/, "");
    if (!hash) return;
    hash.split("&").forEach(function (pair) {
      var bits = pair.split("=");
      var key = decodeURIComponent(bits[0] || "");
      var value = decodeURIComponent((bits[1] || "").replace(/\+/g, " "));
      if (key === "strict") state.strict = value === "1";
      else if (key in state && typeof state[key] === "string") state[key] = value;
    });
  }

  function writeHash() {
    var parts = [];
    ["q", "area", "modality", "company", "role", "platform", "stage", "status", "confidence"].forEach(function (key) {
      if (state[key]) parts.push(key + "=" + encodeURIComponent(state[key]));
    });
    if (state.strict) parts.push("strict=1");
    var next = parts.length ? "#" + parts.join("&") : "";
    try {
      history.replaceState(null, "", location.pathname + location.search + next);
    } catch (error) {
      /* some browsers refuse replaceState on file:// — the page still works */
    }
  }

  function matches(drug) {
    if (state.area && area(drug) !== state.area) return false;
    if (state.modality && drug.modality_group !== state.modality) return false;
    if (state.company && drug.company_clean !== state.company) return false;
    if (state.role && roleOf(drug) !== state.role) return false;
    if (state.platform && platformFamily(drug) !== state.platform) return false;
    if (state.stage && drugStage(drug) !== state.stage) return false;
    if (state.status && drug.derived.status !== state.status) return false;
    if (state.confidence && drug.confidence !== state.confidence) return false;
    if (state.strict && (drug.ai_role_group === "repurposing" || drug.ai_role_group === "personalised_design"))
      return false;
    if (state.q) {
      var needle = state.q.toLowerCase();
      var hay = [
        drug.drug_name,
        (drug.alt_codes || []).join(" "),
        drug.company,
        drug.partner,
        drug.ai_platform,
        drug.ai_role,
        drug.target,
        drug.indication,
        drug.therapy_area,
        drug.modality,
        (drug.trials || [])
          .map(function (t) {
            return t.nct_id + " " + (t.title || "");
          })
          .join(" ")
      ]
        .join(" ")
        .toLowerCase();
      if (hay.indexOf(needle) === -1) return false;
    }
    return true;
  }

  // --------------------------------------------------------------- shells

  function section(id, title, note, bodyHtml) {
    return (
      '<section id="' +
      id +
      '"><div class="section-head"><h2>' +
      esc(title) +
      "</h2><p>" +
      note +
      "</p></div>" +
      bodyHtml +
      "</section>"
    );
  }

  function phaseLegend(stages) {
    return (
      '<div class="legend">' +
      (stages || STAGES).map(function (stage) {
        return (
          '<span class="key"><span class="swatch" style="background:' +
          STAGE_COLOR[stage] +
          '"></span>' +
          esc(stage) +
          "</span>"
        );
      }).join("") +
      "</div>"
    );
  }

  // ------------------------------------------------------------- masthead

  function renderMasthead() {
    var meta = D.meta;
    var warnings = (meta.warnings || []).length;
    var stale = D.drugs.filter(function (d) {
      return d.stale_from;
    }).length;
    var chips = [
      '<span class="chip' +
        (stale ? " stale" : "") +
        '"><span class="dot"></span>Last refresh <strong>' +
        esc(prettyDate(meta.last_refresh)) +
        "</strong></span>",
      '<span class="chip">Previous <strong>' +
        esc(meta.previous_refresh ? prettyDate(meta.previous_refresh) : "none yet") +
        "</strong></span>",
      '<span class="chip">Source <strong>ClinicalTrials.gov API v2</strong></span>',
      '<span class="chip">' + esc(meta.snapshot_count) + " snapshot" + (meta.snapshot_count === 1 ? "" : "s") + " stored</span>",
      '<span class="chip">Seed list <strong>' + esc(D.drugs.length) + " drugs</strong></span>"
    ];
    if (stale) {
      chips.push(
        '<span class="chip stale"><span class="dot"></span>' +
          esc(stale) +
          " drug" +
          (stale === 1 ? "" : "s") +
          " on cached data</span>"
      );
    }
    if (warnings) {
      chips.push('<span class="chip"><a href="#methods">' + esc(warnings) + " refresh warnings</a></span>");
    }
    return (
      '<header class="masthead">' +
      "<h1>AI-designed drugs in clinical development</h1>" +
      '<p class="lede">Every drug on the seed list whose discovery used AI or machine learning materially, ' +
      "tracked against live ClinicalTrials.gov records. Phase and status are re-read at each refresh and stored " +
      "as a dated snapshot, so progress and attrition are both visible. " +
      '<a href="#methods">Inclusion criteria and method</a>.</p>' +
      '<div class="meta-row">' +
      chips.join("") +
      '<button class="theme-toggle" id="theme-toggle" type="button" aria-label="Switch colour theme">Theme</button>' +
      "</div></header>"
    );
  }

  // ---------------------------------------------------------------- tiles

  function renderTiles(rows) {
    var changes = D.changes_since_last_refresh || [];
    var added = changes.filter(function (c) {
      return c.type === "drug_added";
    }).length;
    var phaseMoves = changes.filter(function (c) {
      return c.type === "phase_change";
    });
    var up = phaseMoves.filter(function (c) {
      return c.direction === "up";
    }).length;

    var byStatus = {};
    rows.forEach(function (drug) {
      var key = drug.derived.status;
      byStatus[key] = (byStatus[key] || 0) + 1;
    });
    var late = rows.filter(function (drug) {
      return stageIndex(drugStage(drug)) >= stageIndex("Phase 3");
    }).length;
    var trials = uniq(
      rows.reduce(function (acc, drug) {
        return acc.concat(
          (drug.trials || []).map(function (t) {
            return t.nct_id;
          })
        );
      }, [])
    ).length;
    var withResults = rows.filter(function (drug) {
      return drug.derived.has_results;
    }).length;
    var stageCounts = STAGES.map(function (stage) {
      return (
        esc(stage) +
        " " +
        rows.filter(function (drug) {
          return drugStage(drug) === stage;
        }).length
      );
    }).join(" · ");

    var changeSub = D.meta.previous_refresh
      ? (added ? '<span class="up">' + added + " new</span> · " : "") +
        (up ? '<span class="up">' + up + " advanced</span> · " : "") +
        (phaseMoves.length - up ? phaseMoves.length - up + " moved back · " : "") +
        "since " +
        esc(prettyDate(D.meta.previous_refresh))
      : "first refresh — this snapshot is the baseline";

    var tiles = [
      {
        cls: "tile hero",
        label: "Drugs tracked",
        value: rows.length,
        sub: rows.length === D.drugs.length ? stageCounts : "filtered from " + D.drugs.length + " · " + stageCounts
      },
      {
        label: "Active development",
        value: (byStatus.active || 0) + (byStatus.approved || 0),
        sub:
          (byStatus.discontinued || 0) +
          " discontinued · " +
          (byStatus.deprioritised || 0) +
          " deprioritised · " +
          (byStatus.unclear || 0) +
          " unclear"
      },
      {
        label: "Phase 3 or beyond",
        value: late,
        sub: rows.length ? Math.round((late / rows.length) * 100) + "% of the tracked cohort" : "—"
      },
      {
        label: "Trials linked",
        value: trials,
        sub: withResults + " drug" + (withResults === 1 ? "" : "s") + " with results posted on ClinicalTrials.gov"
      },
      {
        label: "Changes at this refresh",
        value: changes.length,
        sub: changeSub
      }
    ];

    return (
      '<div class="tiles">' +
      tiles
        .map(function (tile) {
          return (
            '<div class="' +
            (tile.cls || "tile") +
            '"><div class="label">' +
            esc(tile.label) +
            '</div><div class="value">' +
            num(tile.value) +
            '</div><div class="sub">' +
            tile.sub +
            "</div></div>"
          );
        })
        .join("") +
      "</div>"
    );
  }

  // -------------------------------------------------------------- filters

  function renderFilters(rows) {
    function options(label, key, values, labeller) {
      return (
        '<select data-filter="' +
        key +
        '" aria-label="' +
        esc(label) +
        '"><option value="">' +
        esc(label) +
        "</option>" +
        values
          .map(function (value) {
            return (
              '<option value="' +
              esc(value) +
              '"' +
              (state[key] === value ? " selected" : "") +
              ">" +
              esc(labeller ? labeller(value) : value) +
              "</option>"
            );
          })
          .join("") +
        "</select>"
      );
    }

    var areas = sortedValues(D.drugs.map(area));
    var modalities = sortedValues(
      D.drugs.map(function (d) {
        return d.modality_group;
      })
    );
    var companies = sortedValues(
      D.drugs.map(function (d) {
        return d.company_clean;
      })
    );
    var statuses = sortedValues(
      D.drugs.map(function (d) {
        return d.derived.status;
      })
    );
    var roles = Object.keys(AI_ROLE).filter(function (key) {
      return D.drugs.some(function (d) {
        return roleOf(d) === key;
      });
    });
    var platforms = sortedValues(D.drugs.map(platformFamily));

    return (
      '<div class="filterbar" role="search">' +
      '<input type="search" id="q" placeholder="Search drug, code, company, target, indication or NCT ID" value="' +
      esc(state.q) +
      '" aria-label="Search">' +
      options("AI role", "role", roles, function (key) {
        return AI_ROLE[key].label;
      }) +
      options("Platform", "platform", platforms) +
      options("Therapy area", "area", areas, titleCase) +
      options("Modality", "modality", modalities, titleCase) +
      options("Company", "company", companies) +
      options("Phase", "stage", STAGES) +
      options("Status", "status", statuses, function (s) {
        return STATUS_LABEL[s] || s;
      }) +
      options("Confidence", "confidence", ["high", "medium", "low"], titleCase) +
      '<label class="toggle"><input type="checkbox" id="strict"' +
      (state.strict ? " checked" : "") +
      "> Strict view</label>" +
      '<button type="button" id="reset">Reset</button>' +
      '<span class="count">Showing <strong>' +
      rows.length +
      "</strong> of " +
      D.drugs.length +
      " drugs</span>" +
      "</div>"
    );
  }

  // --------------------------------------------------------------- funnel

  function funnelCounts(rows) {
    return STAGES.map(function (stage, index) {
      var reached = rows.filter(function (drug) {
        return stageIndex(drugStage(drug)) >= index;
      });
      var atStage = rows.filter(function (drug) {
        return drugStage(drug) === stage;
      });
      var progressed = rows.filter(function (drug) {
        return stageIndex(drugStage(drug)) > index;
      });
      var stoppedHere = atStage.filter(function (drug) {
        return drug.derived.status === "discontinued" || drug.derived.status === "deprioritised";
      });
      return {
        stage: stage,
        reached: reached.length,
        at: atStage.length,
        names: reached.map(function (d) {
          return d.drug_name;
        }),
        progressed: progressed.length,
        stopped: stoppedHere.length,
        known: progressed.length + stoppedHere.length
      };
    });
  }

  function barRows(items, maxValue, colorFor, tipFor) {
    return (
      '<div class="bars">' +
      items
        .map(function (item, index) {
          var width = maxValue ? Math.max(1.5, (item.value / maxValue) * 100) : 0;
          return (
            '<div class="bar-row" data-bar="' +
            index +
            '"><div class="bar-label">' +
            esc(item.label) +
            '</div><div class="bar-track"><div class="bar-fills"><div class="bar-fill" style="width:' +
            width +
            "%;background:" +
            colorFor(item) +
            '"></div></div><span class="bar-value">' +
            num(item.value) +
            (item.suffix || "") +
            "</span></div></div>"
          );
        })
        .join("") +
      "</div>"
    );
  }

  function renderFunnel(rows) {
    var counts = funnelCounts(rows);
    var max = Math.max.apply(
      null,
      counts.map(function (c) {
        return c.reached;
      })
    );
    var bars = barRows(
      counts.map(function (c) {
        return { label: c.stage, value: c.reached, stage: c.stage, at: c.at, names: c.names };
      }),
      max,
      function (item) {
        return STAGE_COLOR[item.stage];
      }
    );

    var table =
      '<details class="table-twin"><summary>Show the numbers</summary><div class="table-scroll"><table>' +
      "<thead><tr><th>Stage</th><th class='num'>Reached this stage</th><th class='num'>Currently at it</th>" +
      "<th class='num'>Progressed beyond</th><th class='num'>Stopped here</th></tr></thead><tbody>" +
      counts
        .map(function (c) {
          return (
            "<tr><td>" +
            esc(c.stage) +
            '</td><td class="num">' +
            c.reached +
            '</td><td class="num">' +
            c.at +
            '</td><td class="num">' +
            c.progressed +
            '</td><td class="num">' +
            c.stopped +
            "</td></tr>"
          );
        })
        .join("") +
      "</tbody></table></div></details>";

    var benchmarks = (D.benchmarks && D.benchmarks.phase_success) || [];
    var sources = (D.benchmarks && D.benchmarks.sources) || {};
    var groups = benchmarks
      .map(function (bench) {
        var observed = counts.filter(function (c) {
          return c.stage === bench.stage;
        })[0];
        if (!observed) return "";
        var enough = observed.known >= 3;
        var cohortRate = enough ? observed.progressed / observed.known : null;
        var bars = [
          {
            name: "This cohort",
            note: enough
              ? observed.progressed + " of " + observed.known + " with a known outcome"
              : "too few resolved outcomes to quote a rate (n=" + observed.known + ")",
            value: cohortRate,
            color: "var(--series-1)"
          },
          {
            name: "AI-discovered (2024)",
            note: bench.ai_range ? Math.round(bench.ai_range[0] * 100) + "–" + Math.round(bench.ai_range[1] * 100) + "%" : "not reported",
            value: bench.ai,
            color: "var(--series-2)"
          },
          {
            name: "Industry 2011–2020",
            note: "all modalities, completed programmes",
            value: bench.industry,
            color: "var(--series-3)"
          }
        ];
        return (
          '<div class="cmp-group"><div class="cmp-title">' +
          esc(bench.label) +
          '</div><div class="cmp-sub">' +
          observed.reached +
          " tracked drugs have reached " +
          esc(bench.stage) +
          "; " +
          observed.known +
          " have a resolved outcome there</div>" +
          bars
            .map(function (bar) {
              var pct = bar.value == null ? null : Math.round(bar.value * 100);
              return (
                '<div class="cmp-bar"><span class="cmp-name"><span class="swatch" style="width:11px;height:11px;border-radius:3px;background:' +
                bar.color +
                '"></span>' +
                esc(bar.name) +
                '</span><div class="cmp-track">' +
                (pct == null
                  ? ""
                  : '<div class="cmp-fill" style="width:' + pct + "%;background:" + bar.color + '"></div>') +
                '</div><span class="cmp-val">' +
                (pct == null ? "n/a" : pct + "%") +
                "</span></div>" +
                '<div class="cmp-bar"><span></span><span class="cell-sub">' +
                esc(bar.note) +
                "</span><span></span></div>"
              );
            })
            .join("") +
          "</div>"
        );
      })
      .join("");

    var caveats = (D.benchmarks && D.benchmarks.caveats) || [];

    return section(
      "funnel",
      "Pipeline funnel",
      "How far the tracked cohort has got, and how its stage-to-stage attrition compares with published success rates.",
      '<div class="grid cols-2">' +
        '<div class="card"><h3>Drugs that have reached each phase</h3>' +
        '<p class="card-note">Cumulative: a Phase 3 drug counts at Phase 1, 2 and 3. Combined phases floor down — a Phase 1/2 trial counts as Phase 1.</p>' +
        bars +
        table +
        "</div>" +
        '<div class="card"><h3>Stage-to-stage progression vs benchmarks</h3>' +
        '<p class="card-note">Cohort rate = drugs that moved past the stage ÷ drugs with a resolved outcome there (moved on, or stopped at it). Drugs still sitting in a phase are excluded, because their outcome is unknown.</p>' +
        groups +
        '<p class="card-note" style="margin-top:14px">' +
        caveats.map(esc).join(" ") +
        "</p>" +
        '<p class="card-note">' +
        Object.keys(sources)
          .map(function (key) {
            var source = sources[key];
            return (
              "<strong>" +
              esc(source.label) +
              ":</strong> " +
              esc(source.citation) +
              ' <a href="' +
              esc(source.url) +
              '" target="_blank" rel="noopener">source</a>'
            );
          })
          .join("<br>") +
        "</p></div></div>"
    );
  }

  // ------------------------------------------------------------- timeline

  function timelineSeries(rows) {
    var events = [];
    rows.forEach(function (drug) {
      var stages = (drug.derived && drug.derived.stage_first_start) || {};
      STAGES.forEach(function (stage) {
        var date = stages[stage];
        if (!date && stage === "Phase 1" && drug.first_in_human_date) date = drug.first_in_human_date;
        if (date) events.push({ stage: stage, year: parseInt(String(date).slice(0, 4), 10), drug: drug });
      });
    });
    if (!events.length) return null;
    var years = events.map(function (e) {
      return e.year;
    });
    var minYear = Math.min.apply(null, years);
    var maxYear = Math.max(
      Math.max.apply(null, years),
      parseInt(String(D.meta.last_refresh || "").slice(0, 4), 10) || 0
    );
    var axis = [];
    for (var year = minYear; year <= maxYear; year++) axis.push(year);

    var series = STAGES.map(function (stage) {
      var perYear = {};
      events
        .filter(function (e) {
          return e.stage === stage;
        })
        .forEach(function (e) {
          perYear[e.year] = (perYear[e.year] || 0) + 1;
        });
      var running = 0;
      var points = axis.map(function (year) {
        running += perYear[year] || 0;
        return { year: year, value: running, added: perYear[year] || 0 };
      });
      return { stage: stage, points: points, total: running };
    }).filter(function (s) {
      return s.total > 0;
    });
    return { axis: axis, series: series };
  }

  function drawTimeline(container, data) {
    if (!data) {
      container.innerHTML = '<p class="empty">No dated trial starts in this slice.</p>';
      return;
    }
    var width = Math.max(320, container.clientWidth);
    var height = 300;
    var pad = { top: 14, right: 74, bottom: 28, left: 34 };
    var plotW = width - pad.left - pad.right;
    var plotH = height - pad.top - pad.bottom;
    var axis = data.axis;
    var maxValue = Math.max.apply(
      null,
      data.series.map(function (s) {
        return s.total;
      })
    );
    var ticks = niceTicks(maxValue);
    var top = ticks[ticks.length - 1];

    function x(year) {
      return axis.length === 1 ? pad.left + plotW / 2 : pad.left + ((year - axis[0]) / (axis.length - 1)) * plotW;
    }
    function y(value) {
      return pad.top + plotH - (value / top) * plotH;
    }

    var svg = [];
    svg.push(
      '<svg class="chart-svg" viewBox="0 0 ' +
        width +
        " " +
        height +
        '" width="' +
        width +
        '" height="' +
        height +
        '" role="img" aria-label="Cumulative number of tracked drugs that had reached each phase, by year">'
    );
    ticks.forEach(function (tick) {
      svg.push('<line class="grid-line" x1="' + pad.left + '" x2="' + (width - pad.right) + '" y1="' + y(tick) + '" y2="' + y(tick) + '"></line>');
      svg.push('<text x="' + (pad.left - 7) + '" y="' + (y(tick) + 4) + '" text-anchor="end">' + tick + "</text>");
    });
    svg.push(
      '<line class="axis-line" x1="' + pad.left + '" x2="' + (width - pad.right) + '" y1="' + y(0) + '" y2="' + y(0) + '"></line>'
    );
    var step = Math.max(1, Math.ceil(axis.length / Math.max(3, Math.floor(plotW / 54))));
    axis.forEach(function (year, index) {
      if (index % step !== 0 && index !== axis.length - 1) return;
      svg.push('<text x="' + x(year) + '" y="' + (height - 9) + '" text-anchor="middle">' + year + "</text>");
    });
    data.series.forEach(function (s) {
      var path = s.points
        .map(function (point, index) {
          return (index ? "L" : "M") + x(point.year).toFixed(1) + " " + y(point.value).toFixed(1);
        })
        .join(" ");
      svg.push('<path class="series-line" d="' + path + '" stroke="' + STAGE_COLOR[s.stage] + '"></path>');
      var last = s.points[s.points.length - 1];
      svg.push(
        '<circle cx="' + x(last.year) + '" cy="' + y(last.value) + '" r="4" fill="' + STAGE_COLOR[s.stage] +
          '" stroke="var(--surface)" stroke-width="2"></circle>'
      );
      svg.push(
        '<text class="end-label" x="' + (x(last.year) + 9) + '" y="' + (y(last.value) + 4) + '">' +
          esc(s.stage) + " " + last.value + "</text>"
      );
    });
    svg.push('<line class="hover-line" id="tl-hover" x1="0" x2="0" y1="' + pad.top + '" y2="' + (pad.top + plotH) + '" style="display:none"></line>');
    svg.push('<rect id="tl-capture" x="' + pad.left + '" y="' + pad.top + '" width="' + plotW + '" height="' + plotH + '" fill="transparent"></rect>');
    svg.push("</svg>");
    container.innerHTML = svg.join("");

    var capture = container.querySelector("#tl-capture");
    var hover = container.querySelector("#tl-hover");
    capture.addEventListener("mousemove", function (event) {
      var box = container.querySelector("svg").getBoundingClientRect();
      var scale = width / box.width;
      var localX = (event.clientX - box.left) * scale;
      var index = Math.round(((localX - pad.left) / plotW) * (axis.length - 1));
      index = Math.max(0, Math.min(axis.length - 1, index));
      var year = axis[index];
      hover.setAttribute("x1", x(year));
      hover.setAttribute("x2", x(year));
      hover.style.display = "";
      showTip(
        event,
        '<div class="tt-title">' +
          year +
          "</div>" +
          data.series
            .map(function (s) {
              var point = s.points[index];
              return (
                '<div class="tt-row"><span><span class="swatch" style="display:inline-block;width:9px;height:9px;border-radius:2px;background:' +
                STAGE_COLOR[s.stage] +
                ';margin-right:6px"></span>' +
                esc(s.stage) +
                "</span><span>" +
                point.value +
                (point.added ? " (+" + point.added + ")" : "") +
                "</span></div>"
              );
            })
            .join("")
      );
    });
    capture.addEventListener("mouseleave", function () {
      hover.style.display = "none";
      hideTip();
    });
  }

  function niceTicks(maxValue) {
    var top = Math.max(1, maxValue);
    var step = Math.pow(10, Math.floor(Math.log10(top)));
    if (top / step < 2) step = step / 2;
    else if (top / step > 5) step = step * 2;
    var ticks = [];
    for (var value = 0; value <= top + step * 0.001; value += step) ticks.push(Math.round(value));
    if (ticks[ticks.length - 1] < top) ticks.push(Math.round(ticks[ticks.length - 1] + step));
    return ticks;
  }

  function renderTimeline(rows) {
    var data = timelineSeries(rows);
    var plotted = {};
    if (data) {
      rows.forEach(function (drug) {
        var stages = (drug.derived && drug.derived.stage_first_start) || {};
        if (Object.keys(stages).length || drug.first_in_human_date) plotted[drug.key] = true;
      });
    }
    var undated = rows.length - Object.keys(plotted).length;
    var table = "";
    if (data) {
      table =
        '<details class="table-twin"><summary>Show the numbers</summary><div class="table-scroll"><table>' +
        "<thead><tr><th>Year</th>" +
        data.series
          .map(function (s) {
            return '<th class="num">' + esc(s.stage) + "</th>";
          })
          .join("") +
        "</tr></thead><tbody>" +
        data.axis
          .map(function (year, index) {
            return (
              "<tr><td>" +
              year +
              "</td>" +
              data.series
                .map(function (s) {
                  return '<td class="num">' + s.points[index].value + "</td>";
                })
                .join("") +
              "</tr>"
            );
          })
          .join("") +
        "</tbody></table></div></details>";
    }

    var moves = [];
    (D.changelog || []).forEach(function (entry) {
      (entry.changes || [])
        .filter(function (change) {
          return change.type === "phase_change";
        })
        .forEach(function (change) {
          moves.push({ date: entry.date, change: change });
        });
    });

    var movesHtml = moves.length
      ? '<ul class="change-list">' +
        moves
          .slice(0, 12)
          .map(function (item) {
            return (
              "<li><span class='tag " +
              (item.change.direction === "up" ? "up" : "down") +
              "'>" +
              esc(prettyDate(item.date)) +
              "</span><span><strong>" +
              esc(item.change.drug) +
              "</strong> " +
              esc(item.change.from) +
              " → " +
              esc(item.change.to) +
              "</span></li>"
            );
          })
          .join("") +
        "</ul>"
      : '<p class="empty">No phase changes recorded between refreshes yet. Phase-change tracking starts from the second snapshot; the chart beside it is built from trial start dates, so it works from day one.</p>';

    return section(
      "timeline",
      "Phase timeline",
      "When the cohort reached each phase. Built from the first ClinicalTrials.gov start date at or above each phase, so it reaches back before this tracker existed.",
      '<div class="grid cols-2">' +
        '<div class="card"><h3>Drugs that had reached each phase, by year</h3>' +
        '<p class="card-note">Cumulative and non-decreasing: a drug stays counted after it has been there, including after discontinuation. ' +
        esc(undated) + ' of ' + rows.length + ' drugs have no dated trial start and are not plotted; ' +
        'ClinicalTrials.gov records no date for a filing, so the Filed stage never appears here.</p>' +
        phaseLegend(
          (data ? data.series : []).map(function (s) {
            return s.stage;
          })
        ) +
        '<div id="timeline-chart"></div>' +
        table +
        "</div>" +
        '<div class="card"><h3>Phase changes between refreshes</h3>' +
        '<p class="card-note">Recorded whenever a refresh sees a drug at a higher or lower phase than the snapshot before it.</p>' +
        movesHtml +
        "</div></div>"
    );
  }

  // ----------------------------------------------------------- breakdowns

  function groupBy(rows, keyFn, limit) {
    var map = {};
    rows.forEach(function (drug) {
      var key = keyFn(drug) || "unknown";
      if (!map[key]) map[key] = [];
      map[key].push(drug);
    });
    var entries = Object.keys(map).map(function (key) {
      return { key: key, rows: map[key] };
    });
    entries.sort(function (a, b) {
      return b.rows.length - a.rows.length || a.key.localeCompare(b.key);
    });
    if (limit && entries.length > limit) {
      var tail = entries.slice(limit - 1);
      entries = entries.slice(0, limit - 1);
      entries.push({
        key: "Other (" + tail.length + " companies)",
        rows: tail.reduce(function (acc, entry) {
          return acc.concat(entry.rows);
        }, [])
      });
    }
    return entries;
  }

  function stackedCard(title, note, entries) {
    var max = Math.max.apply(
      null,
      entries.map(function (entry) {
        return entry.rows.length;
      })
    );
    var bars = entries
      .map(function (entry) {
        var segments = STAGES.map(function (stage) {
          return {
            stage: stage,
            count: entry.rows.filter(function (drug) {
              return drugStage(drug) === stage;
            }).length
          };
        }).filter(function (segment) {
          return segment.count > 0;
        });
        var scale = max ? (entry.rows.length / max) * 100 : 0;
        return (
          '<div class="bar-row"><div class="bar-label">' +
          esc(softBreak(titleCase(entry.key))) +
          '</div><div class="bar-track"><div class="bar-fills" style="width:' +
          scale +
          '%">' +
          segments
            .map(function (segment) {
              return (
                '<div class="bar-fill" data-tip="' +
                esc(titleCase(entry.key) + " · " + segment.stage + ": " + segment.count) +
                '" style="flex:' +
                segment.count +
                ";background:" +
                STAGE_COLOR[segment.stage] +
                '"></div>'
              );
            })
            .join("") +
          '</div><span class="bar-value">' +
          entry.rows.length +
          "</span></div></div>"
        );
      })
      .join("");

    return (
      '<div class="card"><h3>' +
      esc(title) +
      '</h3><p class="card-note">' +
      esc(note) +
      '</p><div class="bars">' +
      bars +
      "</div></div>"
    );
  }

  function renderBreakdowns(rows) {
    if (!rows.length) {
      return section("breakdowns", "Breakdowns", "No drugs match the current filters.", "");
    }
    return section(
      "breakdowns",
      "Breakdowns",
      "How AI was used, and where the cohort sits by therapy area, modality and company — each bar split by the phase its drugs have reached.",
      phaseLegend() +
        '<div class="grid cols-wide">' +
        stackedCard(
          "How AI was used",
          "The claim each row rests on, from the ai_role column of the seed list.",
          groupBy(rows, function (d) {
            return AI_ROLE[roleOf(d)].label;
          })
        ) +
        stackedCard(
          "Modality",
          "Grouped from the free-text modality column in the seed list.",
          groupBy(rows, function (d) {
            return d.modality_group;
          })
        ) +
        stackedCard(
          "Therapy area",
          "Lead indication only; a drug in two areas is counted once.",
          groupBy(rows, area)
        ) +
        stackedCard(
          "Company",
          "Originator or current owner. Smaller companies are folded into Other.",
          groupBy(
            rows,
            function (d) {
              return d.company_clean;
            },
            11
          )
        ) +
        "</div>"
    );
  }

  // ---------------------------------------------------------------- table

  var COLUMNS = [
    { key: "drug", label: "Drug", sort: function (d) { return d.drug_name.toLowerCase(); } },
    { key: "company", label: "Company", sort: function (d) { return d.company_clean.toLowerCase(); } },
    { key: "indication", label: "Target / indication", sort: function (d) { return (d.indication || "").toLowerCase(); } },
    { key: "area", label: "Area", sort: function (d) { return (d.therapy_area || "").toLowerCase(); } },
    { key: "role", label: "AI role", sort: function (d) { return AI_ROLE[roleOf(d)].label; } },
    { key: "platform", label: "Platform", sort: function (d) { return platformFamily(d).toLowerCase(); } },
    { key: "phase", label: "Phase", sort: function (d) { return stageIndex(drugStage(d)) * 10 + (d.derived.phase === "Phase 1/2" || d.derived.phase === "Phase 2/3" ? 1 : 0); } },
    { key: "status", label: "Status", sort: function (d) { return d.derived.status; } },
    { key: "trials", label: "Trials", num: true, sort: function (d) { return d.derived.trial_count || 0; } },
    { key: "confidence", label: "Confidence", sort: function (d) { return { high: 3, medium: 2, low: 1 }[d.confidence] || 0; } }
  ];

  function phasePill(drug) {
    var stage = drugStage(drug);
    return (
      '<span class="pill phase"><span class="dot" style="background:' +
      (STAGE_COLOR[stage] || "var(--muted)") +
      '"></span>' +
      esc(drug.derived.phase || "—") +
      "</span>"
    );
  }

  function statusPill(drug) {
    var status = drug.derived.status;
    return '<span class="pill s-' + esc(status) + '"><span class="dot"></span>' + esc(STATUS_LABEL[status] || status) + "</span>";
  }

  function drugRow(drug) {
    var flags = [];
    if (drug.derived.phase_ahead_of_seed)
      flags.push('<span class="flag" title="ClinicalTrials.gov shows a higher phase than the seed list">ahead of seed list</span>');
    if (drug.needs_verification) flags.push('<span class="flag" title="Seed list marks this row VERIFY">needs verification</span>');
    if (drug.stale_from)
      flags.push('<span class="flag" title="The last refresh could not reach the API for this drug">cached ' + esc(prettyDate(drug.stale_from)) + "</span>");

    return (
      '<tr class="drug-row" data-key="' +
      esc(drug.key) +
      '" tabindex="0" aria-expanded="' +
      (state.open[drug.key] ? "true" : "false") +
      '">' +
      '<td><div class="cell-main">' +
      esc(drug.drug_name) +
      "</div>" +
      (drug.alt_codes && drug.alt_codes.length ? '<div class="cell-sub">' + esc(drug.alt_codes.join("; ")) + "</div>" : "") +
      (flags.length ? '<div class="cell-sub">' + flags.join(" ") + "</div>" : "") +
      "</td>" +
      "<td>" +
      esc(drug.company) +
      (drug.partner ? '<div class="cell-sub">with ' + esc(drug.partner) + "</div>" : "") +
      "</td>" +
      "<td>" +
      esc(drug.target || "—") +
      '<div class="cell-sub">' +
      esc(drug.indication) +
      "</div></td>" +
      "<td>" +
      esc(softBreak(titleCase(drug.therapy_area))) +
      '<div class="cell-sub">' +
      esc(titleCase(drug.modality_group)) +
      "</div></td>" +
      // How AI was used — the claim that puts the row on this list at all.
      '<td><span class="pill role" title="' +
      esc(drug.ai_role || "") +
      '"><span class="dot" style="background:' +
      AI_ROLE[roleOf(drug)].color +
      '"></span>' +
      esc(AI_ROLE[roleOf(drug)].label) +
      "</span></td>" +
      '<td><span title="' +
      esc(drug.ai_platform || "") +
      '">' +
      esc(platformFamily(drug)) +
      "</span></td>" +
      "<td>" +
      phasePill(drug) +
      (drug.derived.phase_basis === "ctgov" ? '<div class="cell-sub">from ClinicalTrials.gov</div>' : "") +
      "</td>" +
      "<td>" +
      statusPill(drug) +
      (drug.derived.status_resolved_from_ctgov ? '<div class="cell-sub">resolved at refresh</div>' : "") +
      "</td>" +
      '<td class="num">' +
      (drug.derived.trial_count || 0) +
      (drug.derived.total_enrolment
        ? '<div class="cell-sub">' + num(drug.derived.total_enrolment) + " enrolled</div>"
        : "") +
      "</td>" +
      '<td><span class="pill c-' +
      esc(drug.confidence) +
      '">' +
      esc(titleCase(drug.confidence)) +
      "</span></td>" +
      "</tr>" +
      (state.open[drug.key] ? detailRow(drug) : "")
    );
  }

  function detailRow(drug) {
    var trials = (drug.trials || []).length
      ? '<div class="table-scroll"><table class="trial-table"><thead><tr><th>Trial</th><th>Phase</th><th>Status</th>' +
        "<th class='num'>Enrolment</th><th>Start</th><th>Primary completion</th><th>Updated</th><th>Results</th><th>Matched by</th></tr></thead><tbody>" +
        drug.trials
          .map(function (trial) {
            return (
              '<tr><td><a href="' +
              esc(trial.url) +
              '" target="_blank" rel="noopener">' +
              esc(trial.nct_id) +
              '</a><div class="cell-sub">' +
              esc((trial.title || "").slice(0, 110)) +
              "</div></td><td>" +
              esc((trial.phases || []).join("/").replace(/PHASE/g, "P").replace(/EARLY_P1/, "Early P1") || "—") +
              "</td><td>" +
              esc(titleCase(ctgStatusLabel(trial.overall_status))) +
              (trial.why_stopped ? '<div class="cell-sub">' + esc(trial.why_stopped.slice(0, 90)) + "</div>" : "") +
              '</td><td class="num">' +
              num(trial.enrollment) +
              (trial.enrollment_type ? '<div class="cell-sub">' + esc(trial.enrollment_type.toLowerCase()) + "</div>" : "") +
              "</td><td>" +
              esc(prettyDate(trial.start_date)) +
              "</td><td>" +
              esc(prettyDate(trial.primary_completion_date)) +
              "</td><td>" +
              esc(prettyDate(trial.last_update_posted)) +
              "</td><td>" +
              (trial.has_results ? "posted" : "—") +
              '</td><td class="cell-sub">' +
              esc(trial.match_reason || "") +
              "</td></tr>"
            );
          })
          .join("") +
        "</tbody></table></div>"
      : '<p class="empty">No ClinicalTrials.gov record is linked yet. The seed list notes a blank NCT means "not verified", not "no trial" — this drug may be registered only on a national registry' +
        (drug.other_registry_ids && drug.other_registry_ids.length
          ? " (" + esc(drug.other_registry_ids.join("; ")) + ")"
          : "") +
        ".</p>";

    return (
      '<tr class="detail-row"><td colspan="' +
      COLUMNS.length +
      '"><dl class="detail-grid">' +
      "<div><dt>AI role</dt><dd>" + esc(drug.ai_role || "—") + "</dd>" +
      "<dt>Platform</dt><dd>" + esc(drug.ai_platform || "—") + "</dd></div>" +
      "<div><dt>Seed-list phase and status</dt><dd>" + esc(drug.seed_phase) + " — " + esc(drug.seed_status) + "</dd>" +
      "<dt>First in human</dt><dd>" + esc(drug.first_in_human_date || "not recorded") + "</dd></div>" +
      "<div><dt>Source checked " + esc(prettyDate(drug.last_checked)) + "</dt><dd>" +
      (drug.source_url
        ? '<a href="' + esc(drug.source_url) + '" target="_blank" rel="noopener">' + esc(drug.source_url.replace(/^https?:\/\//, "").slice(0, 60)) + "</a>"
        : "—") +
      "</dd>" +
      "<dt>Next primary completion</dt><dd>" + esc(prettyDate(drug.derived.next_primary_completion)) + "</dd></div>" +
      "</dl>" +
      trials +
      "</td></tr>"
    );
  }

  function sortRows(rows) {
    var column = COLUMNS.filter(function (c) {
      return c.key === state.sort.key;
    })[0];
    if (!column) return rows;
    var sorted = rows.slice().sort(function (a, b) {
      var left = column.sort(a);
      var right = column.sort(b);
      if (left < right) return -1;
      if (left > right) return 1;
      return a.drug_name.localeCompare(b.drug_name);
    });
    if (state.sort.dir === "desc") sorted.reverse();
    return sorted;
  }

  function renderTable(rows) {
    var sorted = sortRows(rows);
    var head = COLUMNS.map(function (column) {
      var active = state.sort.key === column.key;
      return (
        '<th class="sortable' +
        (column.num ? " num" : "") +
        '" data-sort="' +
        column.key +
        '" scope="col" aria-sort="' +
        (active ? (state.sort.dir === "asc" ? "ascending" : "descending") : "none") +
        '">' +
        esc(column.label) +
        (active ? ' <span class="arrow">' + (state.sort.dir === "asc" ? "▲" : "▼") + "</span>" : "") +
        "</th>"
      );
    }).join("");

    var body = sorted.length
      ? sorted.map(drugRow).join("")
      : '<tr><td colspan="' + COLUMNS.length + '"><p class="empty">No drugs match these filters.</p></td></tr>';

    return section(
      "drugs",
      "Drug table",
      "Every tracked drug, its live trials and the source the seed-list row was checked against. Select a row to open its trials.",
      '<div class="card table-bleed"><div class="section-head" style="margin-bottom:10px"><p>' +
        sorted.length +
        " drug" +
        (sorted.length === 1 ? "" : "s") +
        " listed.</p><button type='button' id='csv' style='margin-left:auto'>Download CSV</button></div>" +
        '<div class="table-scroll"><table id="drug-table"><thead><tr>' +
        head +
        "</tr></thead><tbody>" +
        body +
        "</tbody></table></div></div>"
    );
  }

  // ------------------------------------------------------------ changelog

  function renderChangelog() {
    var entries = (D.changelog || []).filter(function (entry) {
      return entry.change_count > 0 || entry === D.changelog[0];
    });
    var html = entries.length
      ? entries
          .map(function (entry) {
            var list = (entry.changes || []).slice(0, 60);
            return (
              '<div class="changelog-entry"><div class="cl-date">' +
              esc(prettyDate(entry.date)) +
              '</div><div class="cl-sub">' +
              (entry.previous_snapshot
                ? entry.change_count + " change" + (entry.change_count === 1 ? "" : "s") + " since " + esc(prettyDate(entry.previous_snapshot))
                : "baseline snapshot — nothing to compare against") +
              " · " +
              esc((entry.totals && entry.totals.drugs) || 0) +
              " drugs, " +
              esc((entry.totals && entry.totals.trials) || 0) +
              " trials" +
              (entry.warning_count ? " · " + entry.warning_count + " warnings" : "") +
              "</div>" +
              (list.length
                ? '<ul class="change-list">' +
                  list
                    .map(function (change) {
                      var tagClass =
                        change.type === "phase_change"
                          ? change.direction === "up"
                            ? "up"
                            : "down"
                          : change.type === "candidate_flagged"
                          ? "flagwarn"
                          : "";
                      return (
                        '<li><span class="tag ' +
                        tagClass +
                        '">' +
                        esc(CHANGE_LABEL[change.type] || change.type) +
                        "</span><span><strong>" +
                        esc(change.drug || change.nct_id || "") +
                        "</strong> — " +
                        esc(change.detail || "") +
                        (change.nct_id
                          ? ' <a href="https://clinicaltrials.gov/study/' + esc(change.nct_id) + '" target="_blank" rel="noopener">' + esc(change.nct_id) + "</a>"
                          : "") +
                        "</span></li>"
                      );
                    })
                    .join("") +
                  "</ul>" +
                  ((entry.changes || []).length > list.length
                    ? '<p class="empty">… and ' + ((entry.changes || []).length - list.length) + " more in data/changelog.json</p>"
                    : "")
                : "")
            );
          })
          .join("")
      : '<p class="empty">No refreshes recorded yet.</p>';

    return section(
      "changelog",
      "Changelog",
      "What each refresh changed. Stored in data/changelog.json and rebuilt from the dated snapshots in data/snapshots/.",
      '<div class="card">' + html + "</div>"
    );
  }

  // ----------------------------------------------------------- review queue

  function renderReview() {
    var queue = D.review_queue || { candidates: [] };
    var candidates = queue.candidates || [];
    var skipped = (D.meta.sponsors_skipped || [])
      .map(function (item) {
        return item.sponsor + " (" + num(item.trials) + " trials)";
      })
      .join(", ");

    var rows = candidates.length
      ? candidates
          .slice(0, 60)
          .map(function (c) {
            return (
              "<tr><td><a href='" +
              esc(c.url) +
              "' target='_blank' rel='noopener'>" +
              esc(c.nct_id) +
              '</a><div class="cell-sub">' +
              esc((c.title || "").slice(0, 110)) +
              "</div></td><td>" +
              esc(c.lead_sponsor || c.matched_sponsor) +
              (c.lead_sponsor &&
              c.lead_sponsor.toLowerCase().indexOf(String(c.matched_sponsor).toLowerCase()) === -1
                ? '<div class="cell-sub">matched via ' + esc(c.matched_sponsor) + " as collaborator</div>"
                : "") +
              "</td><td>" +
              esc(c.phase || "—") +
              "</td><td>" +
              esc(titleCase(ctgStatusLabel(c.overall_status))) +
              "</td><td>" +
              esc(prettyDate(c.start_date)) +
              "</td><td>" +
              esc((c.interventions || []).slice(0, 2).join("; ")) +
              '<div class="cell-sub">' +
              esc((c.conditions || []).slice(0, 2).join("; ")) +
              "</div></td><td>" +
              esc(prettyDate(c.first_seen)) +
              "</td></tr>"
            );
          })
          .join("")
      : '<tr><td colspan="7"><p class="empty">Nothing waiting for review.</p></td></tr>';

    return section(
      "review",
      "Possible new entries — manual review",
      "Interventional trials from tracked companies that are not linked to any drug on the seed list. " +
        "<strong>None of these are counted anywhere else on this page.</strong> Each one has to be checked against the " +
        "inclusion criteria by hand, then either added to seed-list.csv or recorded in data/review-decisions.json so it stops reappearing.",
      '<div class="card"><p class="card-note">' +
        esc(candidates.length) +
        " candidate" +
        (candidates.length === 1 ? "" : "s") +
        " as of " +
        esc(prettyDate(queue.generated)) +
        ". Sponsors with more than " +
        esc((D.meta.sweep_settings || {}).sponsor_trial_cap || 80) +
        " interventional trials are not swept — their AI assets are tracked by drug code instead" +
        (skipped ? ": " + esc(skipped) : "") +
        ". Trials older than " +
        esc((D.meta.sweep_settings || {}).candidate_lookback_years || 3) +
        " years are ignored.</p>" +
        '<div class="table-scroll"><table><thead><tr><th>Trial</th><th>Sponsor</th><th>Phase</th><th>Status</th>' +
        "<th>Start</th><th>Intervention / condition</th><th>First seen</th></tr></thead><tbody>" +
        rows +
        "</tbody></table></div></div>"
    );
  }

  // --------------------------------------------------------------- methods

  function renderMethods() {
    var meta = D.meta;
    var warnings = meta.warnings || [];
    return section(
      "methods",
      "Inclusion criteria and method",
      "Taken from criteria.md in the repository, so the page and the file can never drift apart.",
      '<div class="grid cols-2">' +
        '<div class="card prose"><h3>What counts as AI-designed</h3>' +
        (D.criteria.inclusion_html || "") +
        "<h3>Confidence</h3>" +
        (D.criteria.confidence_html || "") +
        "</div>" +
        '<div class="card prose"><h3>How this page is built</h3>' +
        "<ul>" +
        "<li>Each refresh queries the ClinicalTrials.gov v2 API for every drug: an intervention search on its codes and INN, plus any NCT IDs curated in the seed list.</li>" +
        "<li>A trial is attached to a drug only when a drug code appears in its title, intervention or protocol ID — or, for a drug whose INN also belongs to a conventionally discovered medicine, when the sponsor matches too.</li>" +
        "<li>Phase is the higher of the seed-list value and what ClinicalTrials.gov shows; rows where the API is ahead are flagged in the table for a human to confirm.</li>" +
        "<li>Each refresh is written to <code>data/snapshots/&lt;date&gt;.json</code> and diffed against the previous one to produce the changelog.</li>" +
        "<li>If the API cannot be reached for a drug, its previous trial data is kept and the row is marked as cached rather than emptied.</li>" +
        "</ul>" +
        "<h3>This refresh</h3><ul>" +
        "<li>Refreshed " + esc(prettyDate(meta.last_refresh)) + " (" + esc(meta.refresh_mode || "live") + "), " + esc(meta.api_calls || 0) + " API calls, " + esc((meta.sponsors_swept || []).length) + " sponsors swept.</li>" +
        "<li>Seed file <code>" + esc(meta.seed_file) + "</code>, checksum <code>" + esc(meta.seed_sha256) + "</code>.</li>" +
        "<li>" + esc(meta.snapshot_count) + " snapshot" + (meta.snapshot_count === 1 ? "" : "s") + " stored; the changelog covers every refresh since the first.</li>" +
        (warnings.length
          ? "<li>Warnings: <ul>" + warnings.map(function (w) { return "<li>" + esc(w) + "</li>"; }).join("") + "</ul></li>"
          : "<li>No warnings.</li>") +
        "</ul>" +
        "<h3>Known gaps</h3>" +
        (D.criteria.gaps_html || "") +
        "<h3>Watchlist — not yet clinical</h3>" +
        (D.criteria.watchlist_html || "") +
        "<h3>Sources</h3>" +
        (D.criteria.sources_html || "") +
        "</div></div>"
    );
  }

  // ----------------------------------------------------------------- csv

  function downloadCsv(rows) {
    var header = [
      "drug_name", "company", "partner", "ai_platform", "modality", "target", "indication",
      "therapy_area", "phase", "phase_basis", "status", "confidence", "ai_role", "trial_count",
      "total_enrolment", "nct_ids", "source_url", "last_refresh"
    ];
    var lines = [header.join(",")];
    rows.forEach(function (drug) {
      var values = [
        drug.drug_name, drug.company, drug.partner, drug.ai_platform, drug.modality, drug.target,
        drug.indication, drug.therapy_area, drug.derived.phase, drug.derived.phase_basis,
        drug.derived.status, drug.confidence, drug.ai_role, drug.derived.trial_count,
        drug.derived.total_enrolment,
        (drug.trials || []).map(function (t) { return t.nct_id; }).join(" "),
        drug.source_url, D.meta.last_refresh
      ];
      lines.push(
        values
          .map(function (value) {
            var text = value == null ? "" : String(value);
            return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
          })
          .join(",")
      );
    });
    var blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "ai-drugs-" + D.meta.last_refresh + ".csv";
    link.click();
    URL.revokeObjectURL(link.href);
  }

  // -------------------------------------------------------------- render

  var timelineData = null;

  function render() {
    var rows = D.drugs.filter(matches);
    timelineData = timelineSeries(rows);
    root.innerHTML =
      renderMasthead() +
      renderTiles(rows) +
      renderFilters(rows) +
      renderFunnel(rows) +
      renderTimeline(rows) +
      renderBreakdowns(rows) +
      renderTable(rows) +
      renderChangelog() +
      renderReview() +
      renderMethods() +
      '<footer class="page-foot">Built from seed-list.csv and the ClinicalTrials.gov v2 API. ' +
      "Trial data is public-domain; the seed list and its confidence calls are editorial. " +
      "Nothing here is investment or clinical advice. Last refresh " +
      esc(prettyDate(D.meta.last_refresh)) +
      ".</footer>";

    drawTimeline(document.getElementById("timeline-chart"), timelineData);
    wire(rows);
    writeHash();
  }

  function wire(rows) {
    var search = document.getElementById("q");
    search.addEventListener("input", function (event) {
      state.q = event.target.value;
      var at = event.target.selectionStart;
      render();
      var next = document.getElementById("q");
      next.focus();
      next.setSelectionRange(at, at);
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-filter]"), function (select) {
      select.addEventListener("change", function (event) {
        state[select.getAttribute("data-filter")] = event.target.value;
        render();
      });
    });
    document.getElementById("strict").addEventListener("change", function (event) {
      state.strict = event.target.checked;
      render();
    });
    document.getElementById("reset").addEventListener("click", function () {
      state.q = state.area = state.modality = state.company = state.stage = state.status = state.confidence = "";
      state.role = state.platform = "";
      state.strict = false;
      render();
    });
    document.getElementById("csv").addEventListener("click", function () {
      downloadCsv(sortRows(rows));
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-sort]"), function (th) {
      th.addEventListener("click", function () {
        var key = th.getAttribute("data-sort");
        if (state.sort.key === key) state.sort.dir = state.sort.dir === "asc" ? "desc" : "asc";
        else state.sort = { key: key, dir: key === "drug" || key === "company" ? "asc" : "desc" };
        render();
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll("tr.drug-row"), function (tr) {
      function toggle() {
        var key = tr.getAttribute("data-key");
        state.open[key] = !state.open[key];
        render();
      }
      tr.addEventListener("click", function (event) {
        if (event.target.closest("a")) return;
        toggle();
      });
      tr.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggle();
        }
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-tip]"), function (element) {
      bindTip(element, esc(element.getAttribute("data-tip")));
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-bar]"), function (element) {
      var label = element.querySelector(".bar-label").textContent;
      var value = element.querySelector(".bar-value").textContent;
      bindTip(element, '<div class="tt-title">' + esc(label) + "</div>" + esc(value) + " drugs have reached this phase");
    });

    var toggle = document.getElementById("theme-toggle");
    toggle.addEventListener("click", function () {
      var current = document.documentElement.getAttribute("data-theme");
      var dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      var next = current ? (current === "dark" ? "light" : "dark") : dark ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try {
        localStorage.setItem("ai-drugs-theme", next);
      } catch (error) {
        /* private mode */
      }
      drawTimeline(document.getElementById("timeline-chart"), timelineData);
    });
  }

  try {
    var saved = localStorage.getItem("ai-drugs-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
  } catch (error) {
    /* private mode */
  }

  readHash();
  render();

  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      drawTimeline(document.getElementById("timeline-chart"), timelineData);
    }, 150);
  });
})();
