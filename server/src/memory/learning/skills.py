"""Autonomous candidate synthesis from evidence-linked task observations.

A candidate is *not* an active skill. No model or historical tool result may
self-certify promotion. The existing evaluator and permission gates remain the
only path to autonomous activation.
"""
from __future__ import annotations

from server.src.observability.operations import observed

import hashlib
import json
import re
from typing import Any

from .tracker import now

from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.representation.skill import (
    Skill, SkillMetadata, SkillRisk, SkillWorkflow, WorkflowStep,
    SkillEvalCase, OutcomeAssertion,
)

READ_ONLY = frozenset({
    'read_file','list_files','list_directory','search_files','search_attachments',
    'web_search','search','grep','ls','glob',
})
# Only local filesystem tooling with isolated evaluator support, never execute or external actions.
SANDBOXABLE = frozenset({'read_file','ls','glob','grep','write_file','edit_file'})
STOPWORDS = frozenset({'the','and','for','with','from','this','that','then','using','into',
                        'task','work','please','new','again','continue','make','file','files'})


def _topics(value: str) -> set[str]:
    return set(re.findall(r'[a-z0-9]{3,}',value.casefold())) - STOPWORDS



class AutonomousSkillDiscovery:
    def __init__(self, db: SQLiteDatabase, skills: Any) -> None:
        self.db, self.skills = db, skills

    @observed("trajecta.skill.discover")
    async def discover(self, task_id: str, traces: list[dict[str,Any]], insight: Any) -> str | None:
        # Synthesize from OBSERVED tool order only; no raw tool args or outputs.
        events = sorted((e for tr in traces if tr.get('outcome') in {'completed','success'}
                         and not (tr.get('metadata') or {}).get('exclude_from_skill_mining')
                         for e in tr.get('steps',[])
                         if isinstance(e,dict) and isinstance(e.get('seq'),int)),
                        key=lambda e:e['seq'])
        cited = set(insight.evidence_event_seqs)
        steps: list[dict[str,Any]] = []
        for event in events:
            data = event.get('data') or {}
            if not isinstance(data,dict):
                continue
            name = data.get('name')
            if not isinstance(name,str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{1,79}',name):
                continue
            if event.get('type')=='tool.call.delta':
                steps.append({'tool':name,'seq':event['seq'],'result':None})
            elif event.get('type')=='tool.result':
                target = next((s for s in reversed(steps) if s['tool']==name and s['result'] is None),None)
                if target is None:
                    target = {'tool':name,'seq':event['seq'],'result':None}
                    steps.append(target)
                target['result']='error' if data.get('status')=='error' else 'observed'
                target['result_seq']=event['seq']
        # A single successful observed tool step plus cited reflection is enough
        # to preserve a CANDIDATE, not to promote it.
        observed = [s for s in steps if s.get('result')=='observed']
        if not observed or not any(s['seq'] in cited or s.get('result_seq') in cited for s in steps):
            return None
        observed=observed[-12:]
        names=[s['tool'] for s in observed]
        goal = str(traces[0].get('task_goal') or traces[0].get('goal') or '')[:250]
        if not goal:
            return None
        risk = (SkillRisk.READ_ONLY if set(names)<=READ_ONLY
                else SkillRisk.SANDBOXED if set(names)<=SANDBOXABLE
                else SkillRisk.SENSITIVE)
        description = re.sub(r'\s+',' ',goal).strip()
        # Avoid memorizing user instructions containing secrets as procedures.
        if re.search(r'(?i)(password\s*[:=]|api[_ -]?key\s*[:=]|bearer\s+[a-z0-9]{12,})',goal):
            return None
        identifier = hashlib.sha256(json.dumps({
            'goal':sorted(re.findall(r'[a-z0-9]{3,}',description.casefold()))[:30],
            'tools':names,
        },sort_keys=True).encode()).hexdigest()[:10]
        stem = '-'.join(re.findall(r'[a-z0-9]+',description.casefold())[:5])[:48].strip('-') or 'learned-workflow'
        name = f'{stem[:48]}-{identifier}'
        name = name[:64].rstrip('-')
        prior = await self.db.fetchone(
            'SELECT candidate_id FROM learned_skill_sources WHERE task_id=? AND fingerprint=?',
            (task_id,identifier),
        )
        if prior:
            await self.refresh_candidate(prior['candidate_id'])
            return prior['candidate_id']
        already = await self.db.fetchone(
            'SELECT id FROM skill_candidates WHERE name=? ORDER BY created_at DESC LIMIT 1',
            (name,),
        )
        if already:
            await self.db.execute(
                'INSERT OR IGNORE INTO learned_skill_sources(task_id,candidate_id,fingerprint,created_at) VALUES (?,?,?,?)',
                (task_id,already['id'],identifier,now()),
            )
            await self.refresh_candidate(already['id'])
            return already['id']
        cited_notes = insight.content
        workflows = [WorkflowStep(
            instruction=(f'Use {s["tool"]} for the relevant step of this task. '
                         'Determine current inputs from the request; do not replay recorded arguments.'),
            tool_names=[s['tool']],
            success_signal='Independently inspect the current result; past non-error outputs are not proof.',
        ) for s in observed]
        skill = Skill(
            name=name,description=description,
            instructions=(f'Applicability: {description}\n\n'
                          f'Historical insight (not verified guidance): {cited_notes}\n\n'
                          'Follow the observed tool sequence only if appropriate. Check current '
                          'scope, inputs, safety policy, and outcome. Recover from tool errors '
                          'without overriding permissions. Do not assume historical success.'),
            workflow=SkillWorkflow(trigger=description,steps=workflows,
                success_criteria=['Verify the requested outcome independently before claiming success'],
                failure_recovery=['Inspect errors and adapt; do not blindly repeat failing calls']),
            metadata=SkillMetadata(
                source_trajectory_ids=[t['id'] for t in traces],risk=risk,
                mining_fingerprint=identifier,
                notes={'autonomous_discovery':True,'task_id':task_id,
                       'verification':'awaiting_independent_cases',
                       'evidence_seqs':sorted(cited),
                       'approval_required':False,'permissions_unchanged':True}),
        )
        candidate = await self.skills.repository.create_candidate(skill)
        await self.db.execute(
            'INSERT OR IGNORE INTO learned_skill_sources(task_id,candidate_id,fingerprint,created_at) VALUES (?,?,?,?)',
            (task_id,candidate['id'],identifier,now()),
        )
        await self.refresh_candidate(candidate['id'])
        return candidate['id']

    async def refresh_pending(self, *, limit: int = 100) -> None:
        """Give old candidates another chance when later tasks gain verified evidence."""
        for record in await self.skills.repository.list_candidates(status='candidate',limit=limit):
            if (record.get('metadata') or {}).get('skill_bundle',{}).get('metadata',{}).get('notes',{}).get('autonomous_discovery'):
                await self.refresh_candidate(record['id'])

    @observed("trajecta.skill.refresh_candidate")
    async def refresh_candidate(self, candidate_id: str) -> None:
        candidate = await self.skills.repository.get_candidate(candidate_id)
        if not candidate or candidate['status'] not in {'candidate'}:
            return
        skill = await self.skills.repository.get_candidate_skill(candidate_id)
        if not skill or not skill.metadata.notes.get('autonomous_discovery'):
            return
        task_id = str(skill.metadata.notes.get('task_id') or '')
        risk = skill.metadata.risk
        names = skill.workflow.tool_names
        description = skill.description
        if not task_id or not names:
            return
        reason = 'awaiting_verified_cases'
        # Collect independently captured file-state assertions with a linked
        # initial fixture and an explicit user confirmation. Mere thumbs-up,
        # tool status or LLM reflection is never enough.
        # Only deterministic assertions from trusted evidence qualify.
        cases: list[SkillEvalCase] = []
        rows = await self.db.fetch(
            """SELECT logical_task_id,goal,tool_names,evidence,scope FROM episodes
               WHERE outcome_verified=1 AND user_id='local' AND scope='local'
                 AND logical_task_id<>? ORDER BY created_at DESC LIMIT 200""",
            (task_id,),
        )
        topics = _topics(description)
        for row in rows:
            # Never replay unrelated historical work, even if it has a verified
            # outcome. Each held-out case must describe the *actual* other task.
            other_topics = _topics(str(row['goal']))
            overlap = len(topics & other_topics)
            if overlap < 2 or overlap / max(1,len(topics | other_topics)) < 0.30:
                continue
            try:
                row_tools = json.loads(row['tool_names'] or '[]')
                evidence = json.loads(row['evidence'] or '{}')
            except (ValueError,TypeError):
                continue
            if not isinstance(row_tools,list) or not set(names).issubset(set(row_tools)):
                continue
            if evidence.get('verification') != 'user_confirmed_observed_local_state':
                continue
            assertions = evidence.get('outcome_assertions')
            fixture_id = evidence.get('replay_fixture_id')
            source_id = evidence.get('verification_trajectory_id')
            if not isinstance(assertions,list) or not assertions or not isinstance(fixture_id,str) or not isinstance(source_id,str):
                continue
            # Only already verified records qualify; validate again against the
            # current schema before creating a replay test.
            try:
                validated = [OutcomeAssertion.model_validate(item) for item in assertions]
            except (ValueError,TypeError):
                continue
            cases.append(SkillEvalCase(
                id=f'heldout-{source_id[:16]}', name='Verified local task',
                task=str(row['goal'])[:1000],source_trajectory_id=source_id,
                outcome_assertions=validated,allowed_tools=names,
                metadata={'evidence_source':'user_confirmed_observed_local_state',
                          'requires_replay_fixture':True,
                          'replay_fixture_id':fixture_id},
            ))
            if len(cases)>=10:
                break
        minimum = skill.eval_config.minimum_graded_cases
        # Evaluate only operations supported by the isolated replay backend.
        safe = risk in {SkillRisk.READ_ONLY,SkillRisk.SANDBOXED} and set(names)<=SANDBOXABLE
        if not safe:
            reason = 'sensitive_tools_require_manual_evidence'
        elif len(cases) < minimum:
            reason = 'awaiting_verified_cases'
        else:
            reason = 'ready_for_evaluation'
        unchanged = (candidate['metadata'].get('evaluation_blocker') == reason
                     and candidate['metadata'].get('verified_heldout_cases') == len(cases)
                     and len(skill.eval_cases) == len(cases)
                     and all(old.model_dump(mode='json') == new.model_dump(mode='json')
                             for old,new in zip(skill.eval_cases,cases)))
        if unchanged and reason != 'ready_for_evaluation':
            return
        skill.eval_cases=cases
        skill.metadata.notes['verification']=reason
        await self.skills.repository.update_candidate(
            candidate_id,skill=skill,
            extra_metadata={'evaluation_blocker':reason,
                            'verified_heldout_cases':len(cases),
                            'required_heldout_cases':minimum},
        )
        if reason != 'ready_for_evaluation':
            return
        signature = hashlib.sha256(json.dumps(
            [case.model_dump(mode='json') for case in cases],
            sort_keys=True,ensure_ascii=False,
        ).encode()).hexdigest()
        if candidate['metadata'].get('auto_evaluation_signature') == signature:
            return  # Do not re-run expensive failed/needs-review evaluations on every chat.
        await self.skills.repository.update_candidate(
            candidate_id,extra_metadata={'auto_evaluation_signature':signature},
        )
        # Evaluator persists a detailed report and owns candidate state.
        try:
            report = await self.skills.evaluator.evaluate(candidate_id)
            if report.verdict=='pass':
                await self.skills.promoter.promote(candidate_id=candidate_id,evaluation_id=report.id)
            else:
                current = await self.skills.repository.get_candidate(candidate_id)
                if current and current['status']=='candidate':
                    await self.skills.repository.update_candidate(
                        candidate_id,extra_metadata={'evaluation_blocker':'evaluation_needs_review'},
                    )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception('Autonomous skill evaluation failed')
            # Failed evaluations remain visible. A crash must not grant a skill.
            current = await self.skills.repository.get_candidate(candidate_id)
            if current and current['status']=='candidate':
                await self.skills.repository.update_candidate(
                    candidate_id,extra_metadata={'evaluation_blocker':'evaluation_error',
                                                 'evaluation_error':str(exc)[:300]},
                )
