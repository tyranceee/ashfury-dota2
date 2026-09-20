"use strict";

function createLogger(write = console.log) {
  const emit = (level, event, fields = {}) => {
    write(JSON.stringify({
      timestamp: new Date().toISOString(),
      level,
      event,
      ...fields,
    }));
  };

  return {
    info(event, fields) {
      emit("info", event, fields);
    },
    error(event, fields) {
      emit("error", event, fields);
    },
  };
}

module.exports = { createLogger };
