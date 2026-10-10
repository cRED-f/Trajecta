"""Autonomous candidate synthesis from evidence-linked task observations.

A candidate is *not* an active skill. No model or historical tool result may
self-certify promotion. The existing evaluator and permission gates remain the
only path to autonomous activation.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.representation.skill import (
    Skill, SkillMetadata, SkillRisk, SkillWorkflow, WorkflowStep,
    SkillEvalCase, OutcomeAssertion,
)

READ_ONLY = frozenset({
    'read_file','list_files','list_directory','search_files','search_attachments',
    'web_search','search','grep',
})


class AutonomousSkillDiscovery:
    def __init__(self, db: SQLiteDatabase, skills: Any) -> None:
        self.db, self.skills = db, skills

    async def discover(self, task_id: str, traces: list[dict[str,Any]], insight: Any) -> str | None:
        # Synthesize from OBSERVED tool order only; no raw tool args or outputs.
        events = sorted((e for tr in traces for e in tr.get('steps',[])
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
        risk = SkillRisk.READ_ONLY if set(names)<=READ_ONLY else SkillRisk.SENSITIVE
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
            return prior['candidate_id']
        already = await self.db.fetchone(
            'SELECT id FROM skill_candidates WHERE name=? ORDER BY created_at DESC LIMIT 1',
            (name,),
        )
        if already:
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
        from .tracker import now
        await self.db.execute(
            'INSERT OR IGNORE INTO learned_skill_sources(task_id,candidate_id,fingerprint,created_at) VALUES (?,?,?,?)',
            (task_id,candidate['id'],identifier,now()),
        )
        # Collect independently verified (not merely rated) held-out cases.
        # Only deterministic assertions from trusted evidence qualify.
        cases = []
        rows = await self.db.fetch(
            '''SELECT source_trajectory_id,evidence FROM episodes
               WHERE outcome_verified=1 AND user_id='local' AND scope='local'
                 AND logical_task_id<>? ORDER BY created_at DESC LIMIT 15''',
            (task_id,),
        )
        for row in rows:
            evidence = json.loads(row['evidence'] or '{}')
            assertions = evidence.get('outcome_assertions') or []
            if not isinstance(assertions,list) or not assertions:
                continue
            try:
                validated = [OutcomeAssertion.model_validate(item) for item in assertions]
                cases.append(SkillEvalCase(
                    id=f'heldout-{len(cases)+1}',name='Verified independent task',
                    task=description,source_trajectory_id=row['source_trajectory_id'],
                    outcome_assertions=validated,allowed_tools=names,
                    metadata={'evidence_source':'independently_verified_task'},
                ))
            except ValueError:
                continue
            if len(cases)>=3:
                break
        if len(cases)>=2 and risk==SkillRisk.READ_ONLY:
            skill.eval_cases=cases
            await self.skills.repository.update_candidate(candidate['id'],skill=skill)
            # The model evaluator is background-only; do not promote on a weak
            # or incomplete comparison. Failures leave a reviewable candidate.
            try:
                report=await self.skills.evaluator.evaluate(candidate['id'])
                if report.verdict=='pass':
                    await self.skills.promoter.promote(candidate_id=candidate['id'],evaluation_id=report.id)
            except Exception:
                # A failed evaluation is never an authorization to activate.
                import logging
                logging.getLogger(__name__).exception('Autonomous skill evaluation failed')
        return candidate['id']
