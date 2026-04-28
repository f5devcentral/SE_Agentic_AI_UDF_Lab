# BIG-IP iRule for Logging and Tracing (W3C Trace Context)

when HTTP_REQUEST {
    # Extract traceparent header or generate if missing
    set traceparent [HTTP::header "traceparent"]
    if { $traceparent eq "" } {
        # Generate a new traceparent (simple version, not full spec)
        set trace_id [format "%032x" [expr { int(rand()*0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF) }]]
        set parent_id [format "%016x" [expr { int(rand()*0xFFFFFFFFFFFFFFFF) }]]
        set traceparent "00-$trace_id-$parent_id-01"
        HTTP::header insert "traceparent" $traceparent
    }
    set tracestate [HTTP::header "tracestate"]
    set log_msg "BIGIP_TRACE|[IP::client_addr]|[HTTP::uri]|$traceparent|$tracestate"
    HSL::send $hsl $log_msg
}
when RULE_INIT {
    set static::hsl_pool "syslog_server_pool"
}
when CLIENT_ACCEPTED {
    set hsl [HSL::open -proto UDP -pool $static::hsl_pool]
}

# Replace syslog_server_pool with your Fluentd/Fluent Bit syslog pool name.
