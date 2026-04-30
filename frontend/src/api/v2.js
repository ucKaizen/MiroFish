import service from './index'

// All endpoints under /api/v2/* — see backend/app/v2/api.py.

export function listStudies() {
  return service({ url: '/api/v2/studies', method: 'get' })
}

export function registerStudyFromDisk(path) {
  return service({ url: '/api/v2/studies/from-disk', method: 'post', data: { path } })
}

export function startRun({ study_id, rounds = 2, skip_neo4j = false, no_llm_narrator = false }) {
  return service({
    url: '/api/v2/runs',
    method: 'post',
    data: { study_id, rounds, skip_neo4j, no_llm_narrator }
  })
}

export function listRuns() {
  return service({ url: '/api/v2/runs', method: 'get' })
}

export function getRun(run_id) {
  return service({ url: `/api/v2/runs/${run_id}`, method: 'get' })
}

export function getRunLog(run_id) {
  return service({ url: `/api/v2/runs/${run_id}/log`, method: 'get' })
}

// Returns plain markdown — bypass the response interceptor's success check.
export function getRunReportMarkdown(run_id) {
  return service({
    url: `/api/v2/runs/${run_id}/report`,
    method: 'get',
    responseType: 'text',
    transformResponse: [(data) => data]
  })
}

export function listGraphs() {
  return service({ url: '/api/v2/graphs', method: 'get' })
}

export function getGraph(graph_id, opts = {}) {
  return service({
    url: `/api/v2/graphs/${graph_id}`,
    method: 'get',
    params: opts
  })
}
