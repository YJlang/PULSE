package com.example.pulse_spring.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/*
 * Spring Boot 4는 기본 JSON 스택을 Jackson 3(tools.jackson.*)으로 전환했고,
 * JacksonAutoConfiguration도 그쪽 타입의 빈만 자동 등록한다.
 * 이 프로젝트는 여전히 classic Jackson 2(com.fasterxml.jackson.databind.ObjectMapper) API를
 * 직접 사용하는 코드(ReviewManagementService 등)가 있어, 해당 타입의 빈을 명시적으로 등록해
 * 서비스가 `new ObjectMapper()` 대신 주입받아 쓸 수 있게 한다.
 */
@Configuration
public class JacksonConfig {
    @Bean
    public ObjectMapper objectMapper() {
        return new ObjectMapper();
    }
}
